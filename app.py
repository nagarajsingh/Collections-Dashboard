import os
import json
from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ai_extractor import (
    ai_extract_services_from_text,
    ai_extract_code_pull_details,
)
from azure_devops import (
    resolve_pipeline_id,
    trigger_image_pipeline,
    trigger_code_pull_pipeline_item,
    trigger_build_pipeline_item,
    get_run_status,
)
from database import (
    init_db,
    insert_deployment,
    get_deployments_by_date,
    update_build_status,
    insert_code_pull_run,
    get_code_pull_runs_by_date,
    update_code_pull_status,
    insert_build_run,
    get_build_runs_by_date,
    update_build_run_status,
)
from pdf_utils import (
    extract_services_from_document,
    extract_text_from_document,
)

load_dotenv()

st.set_page_config(page_title="AI Document Extractor", layout="wide")
st.title("AI Document Extractor")

with open("pipeline_mapping.json", "r", encoding="utf-8") as f:
    PIPELINE_MAPPING = json.load(f)

with open("build_pipeline_mapping.json", "r", encoding="utf-8") as f:
    BUILD_PIPELINE_MAPPING = json.load(f)


def check_config():
    required = [
        "AZDO_ORG",
        "AZDO_PROJECT",
        "AZDO_PAT",
        "DB_HOST",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    ]

    missing = [name for name in required if not os.getenv(name)]

    if missing:
        st.error(f"Missing environment variables: {', '.join(missing)}")
        st.stop()


def trigger_service(service, vendor_image, environment, use_vendor_image):
    pipeline_name = PIPELINE_MAPPING.get(environment, {}).get(service)

    if not pipeline_name:
        return {
            "Service": service,
            "vendorImage": vendor_image,
            "Environment": environment,
            "useVendorImage": use_vendor_image,
            "Pipeline Name": "",
            "Pipeline ID": "",
            "Run ID": "",
            "State": "not_triggered",
            "Result": "mapping_missing",
            "URL": "",
            "Error": f"No pipeline mapping found for service '{service}' in environment '{environment}'",
        }

    pipeline_id = resolve_pipeline_id(pipeline_name)

    if not pipeline_id:
        return {
            "Service": service,
            "vendorImage": vendor_image,
            "Environment": environment,
            "useVendorImage": use_vendor_image,
            "Pipeline Name": pipeline_name,
            "Pipeline ID": "",
            "Run ID": "",
            "State": "not_triggered",
            "Result": "pipeline_not_found",
            "URL": "",
            "Error": f"Could not find pipeline ID for: {pipeline_name}",
        }

    try:
        data = trigger_image_pipeline(
            pipeline_id=pipeline_id,
            vendor_image=vendor_image,
            use_vendor_image=use_vendor_image,
        )

        return {
            "Service": service,
            "vendorImage": vendor_image,
            "Environment": environment,
            "useVendorImage": use_vendor_image,
            "Pipeline Name": pipeline_name,
            "Pipeline ID": pipeline_id,
            "Run ID": data.get("id"),
            "State": data.get("state", "inProgress"),
            "Result": data.get("result", ""),
            "URL": data.get("_links", {}).get("web", {}).get("href", ""),
            "Error": "",
        }

    except Exception as exc:
        return {
            "Service": service,
            "vendorImage": vendor_image,
            "Environment": environment,
            "useVendorImage": use_vendor_image,
            "Pipeline Name": pipeline_name,
            "Pipeline ID": pipeline_id,
            "Run ID": "",
            "State": "failed_to_trigger",
            "Result": "failed",
            "URL": "",
            "Error": str(exc),
        }


def build_created_branch_name(profinch_branch, build_number, app_name):
    return f"profinch/{profinch_branch}-{build_number}-{app_name}"


def refresh_code_pull_status(selected_date):
    df = get_code_pull_runs_by_date(selected_date)

    for _, row in df.iterrows():
        if pd.isna(row["pipeline_id"]) or pd.isna(row["run_id"]):
            continue

        if str(row.get("result") or "").lower() in ["succeeded", "failed", "canceled", "cancelled"]:
            continue

        status = get_run_status(int(row["pipeline_id"]), int(row["run_id"]))

        update_code_pull_status(
            row_id=row["id"],
            status=status["state"],
            result=status["result"],
            run_url=status["url"] or row.get("run_url", ""),
            error=status["error"],
        )


def refresh_build_status(selected_date):
    df = get_build_runs_by_date(selected_date)

    for _, row in df.iterrows():
        if pd.isna(row["pipeline_id"]) or pd.isna(row["run_id"]):
            continue

        if str(row.get("result") or "").lower() in ["succeeded", "failed", "canceled", "cancelled"]:
            continue

        status = get_run_status(int(row["pipeline_id"]), int(row["run_id"]))

        update_build_run_status(
            row_id=row["id"],
            status=status["state"],
            result=status["result"],
            run_url=status["url"] or row.get("run_url", ""),
            error=status["error"],
        )


def render_gtb_dashboard(selected_date):
    st.subheader("GTB Application Deployment")

    if st.button("Get / Refresh Code Pull and Build Status"):
        refresh_code_pull_status(selected_date)
        refresh_build_status(selected_date)
        st.success("GTB status refreshed.")

    code_pull_df = get_code_pull_runs_by_date(selected_date)

    st.markdown("### Code Pull Runs")

    if code_pull_df.empty:
        st.info("No code-pull runs found for selected date.")
    else:
        st.dataframe(
            code_pull_df,
            use_container_width=True,
            column_config={
                "run_url": st.column_config.LinkColumn("Code Pull", display_text="Open"),
            },
        )

    build_df = get_build_runs_by_date(selected_date)

    st.markdown("### Build Runs")

    if build_df.empty:
        st.info("No build runs found for selected date.")
    else:
        st.dataframe(
            build_df,
            use_container_width=True,
            column_config={
                "run_url": st.column_config.LinkColumn("Build", display_text="Open"),
            },
        )

    ready_for_build = code_pull_df[
        (code_pull_df["result"] == "succeeded")
    ] if not code_pull_df.empty else pd.DataFrame()

    if ready_for_build.empty:
        st.info("No successful code-pull runs available for build trigger.")
        return

    st.markdown("### Trigger Build Pipeline")

    build_input_df = ready_for_build.copy()
    build_input_df.insert(0, "Trigger Build", False)

    if not build_input_df.empty:
        build_input_df.loc[0, "Trigger Build"] = True

    edited_df = st.data_editor(
        build_input_df,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "Trigger Build": st.column_config.CheckboxColumn("Trigger Build", default=False)
        },
    )

    selected_df = edited_df[edited_df["Trigger Build"] == True]

    if st.button("Trigger Build Pipeline for Selected"):
        results = []

        for _, row in selected_df.iterrows():
            app = row.get("pipeline_application")
            build_config = BUILD_PIPELINE_MAPPING.get(app)

            if not build_config:
                results.append({
                    "app": app,
                    "status": "failed",
                    "error": f"No build pipeline mapping found for app: {app}",
                })
                continue

            item = {
                "build_branch": row.get("build_branch"),
                "war_files": row.get("war_files") or "None",
                "jar_files": row.get("jar_files") or "None",
                "deploy_type": row.get("deploy_type") or "Regular",
            }

            try:
                pipeline_id, result = trigger_build_pipeline_item(item, build_config)

                insert_build_run(
                    code_pull_row=row,
                    pipeline_id=pipeline_id,
                    result=result,
                )

                results.append({
                    "app": app,
                    "build_branch": item["build_branch"],
                    "war_files": item["war_files"],
                    "jar_files": item["jar_files"],
                    "deploy_type": item["deploy_type"],
                    "status": "triggered",
                    "run_id": result.get("id"),
                    "url": result.get("_links", {}).get("web", {}).get("href", ""),
                    "error": "",
                })

            except Exception as exc:
                results.append({
                    "app": app,
                    "status": "failed",
                    "error": str(exc),
                })

        st.subheader("Build Trigger Results")
        st.dataframe(pd.DataFrame(results), use_container_width=True)


def render_collections_dashboard(selected_date, use_vendor_image):
    st.subheader("Collections Deployment Dashboard")

    if st.button("Get / Refresh Collections Pipeline Status"):
        dashboard = get_deployments_by_date(selected_date)

        for _, row in dashboard.iterrows():
            if pd.notna(row["pipeline_id"]) and pd.notna(row["run_id"]):
                status = get_run_status(
                    int(row["pipeline_id"]),
                    int(row["run_id"]),
                )

                update_build_status(
                    row["id"],
                    status["state"],
                    status["result"],
                    status["url"] or row["build_url"],
                    status["error"],
                )

        st.success("Collections build status refreshed.")

    dashboard_df = get_deployments_by_date(selected_date)

    if dashboard_df.empty:
        st.info("No collections deployments found for selected date.")
        return

    st.dataframe(
        dashboard_df,
        use_container_width=True,
        column_config={
            "build_url": st.column_config.LinkColumn("Build", display_text="Open"),
        },
    )

    dashboard_df.insert(0, "retrigger", False)

    edited_dashboard_df = st.data_editor(
        dashboard_df,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "retrigger": st.column_config.CheckboxColumn("Retrigger", default=False)
        },
    )

    if st.button("Retrigger Selected Collection Services"):
        selected_retry_df = edited_dashboard_df[
            edited_dashboard_df["retrigger"] == True
        ]

        if selected_retry_df.empty:
            st.warning("Select at least one service to retrigger.")
        else:
            for _, row in selected_retry_df.iterrows():
                result = trigger_service(
                    row["service_name"],
                    row["vendor_image"],
                    row["environment"],
                    use_vendor_image,
                )

                insert_deployment(result)

            st.success("Retrigger completed.")


check_config()
init_db()

extraction_type = st.radio(
    "Extraction Type",
    [
        "Image Tag Extraction",
        "Code Pull Extraction",
    ],
    horizontal=True,
)

environment = None
use_vendor_image = True

if extraction_type == "Image Tag Extraction":
    environment = st.selectbox("Environment", list(PIPELINE_MAPPING.keys()))
    use_vendor_image = st.checkbox("useVendorImage", value=True)

use_ai_extractor = st.checkbox("Use AI Agent for unstructured documents", value=True)

selected_date = st.date_input("Select activity date", value=date.today())

uploaded_file = st.file_uploader(
    "Upload Release Document",
    type=["pdf", "docx"]
)

if uploaded_file and extraction_type == "Code Pull Extraction":
    document_text = extract_text_from_document(uploaded_file)

    if st.button("Extract Code Pull Details"):
        try:
            code_pull_json = ai_extract_code_pull_details(document_text)
            st.session_state["code_pull_json"] = code_pull_json
            st.success("Code pull/build details extracted.")
        except Exception as exc:
            st.error(f"Code pull extraction failed: {str(exc)}")

    if "code_pull_json" in st.session_state:
        st.subheader("Extracted Code Pull + Build JSON")

        json_text = st.text_area(
            "Validate / edit JSON before triggering code-pull pipeline",
            value=json.dumps(st.session_state["code_pull_json"], indent=2),
            height=400,
        )

        try:
            validated_json = json.loads(json_text)
            st.json(validated_json)

            items = validated_json.get("items", [])

            if not items:
                st.warning("No code-pull items found in JSON.")
            else:
                item_df = pd.DataFrame(items)
                item_df.insert(0, "Select", True)

                edited_items_df = st.data_editor(
                    item_df,
                    use_container_width=True,
                    num_rows="dynamic",
                    column_config={
                        "Select": st.column_config.CheckboxColumn("Select", default=True),
                        "list_only": st.column_config.CheckboxColumn("LIST_ONLY", default=False),
                    },
                )

                selected_items_df = edited_items_df[edited_items_df["Select"] == True]

                if st.button("Trigger Selected Code Pull Pipelines"):
                    results = []

                    for _, row in selected_items_df.iterrows():
                        item = row.drop(labels=["Select"]).to_dict()

                        try:
                            pipeline_id, result = trigger_code_pull_pipeline_item(item)

                            build_number = result.get("name", "")

                            source_branch = build_created_branch_name(
                                profinch_branch=item.get("branch_name"),
                                build_number=build_number,
                                app_name=item.get("pipeline_application"),
                            )

                            insert_code_pull_run(
                                item=item,
                                pipeline_id=pipeline_id,
                                result=result,
                                source_branch=source_branch,
                                extracted_json=validated_json,
                            )

                            results.append({
                                "app": item.get("pipeline_application"),
                                "branch": item.get("branch_name"),
                                "status": "triggered",
                                "run_id": result.get("id"),
                                "url": result.get("_links", {}).get("web", {}).get("href", ""),
                                "error": "",
                            })

                        except Exception as exc:
                            results.append({
                                "app": item.get("pipeline_application"),
                                "branch": item.get("branch_name"),
                                "status": "failed",
                                "error": str(exc),
                            })

                    st.subheader("Code Pull Trigger Results")
                    st.dataframe(pd.DataFrame(results), use_container_width=True)

        except Exception as exc:
            st.error(f"Invalid JSON: {str(exc)}")


if uploaded_file and extraction_type == "Image Tag Extraction":
    extracted_data = extract_services_from_document(uploaded_file)

    if not extracted_data and use_ai_extractor:
        st.info("Rule-based extraction did not find services. Running AI Agent extraction...")

        try:
            document_text = extract_text_from_document(uploaded_file)

            if not document_text:
                st.warning("Could not extract text from document. AI extraction skipped.")
            else:
                extracted_data = ai_extract_services_from_text(document_text)
                st.success(f"AI Agent extracted {len(extracted_data)} services.")

        except Exception as exc:
            st.error(f"AI extraction failed: {str(exc)}")

    if extracted_data:
        df = pd.DataFrame(extracted_data)

        edited_df = st.data_editor(
            df,
            use_container_width=True,
            num_rows="fixed",
            column_config={
                "Select": st.column_config.CheckboxColumn("Select", default=True)
            },
        )

        selected_df = edited_df[edited_df["Select"] == True]

        if st.button("Trigger Selected Service Pipelines"):
            for _, row in selected_df.iterrows():
                result = trigger_service(
                    service=row["Service"],
                    vendor_image=row["vendorImage"],
                    environment=environment,
                    use_vendor_image=use_vendor_image,
                )

                insert_deployment(result)

            st.success("Trigger request completed.")


st.divider()

if extraction_type == "Code Pull Extraction":
    render_gtb_dashboard(selected_date)

if extraction_type == "Image Tag Extraction":
    render_collections_dashboard(selected_date, use_vendor_image)
