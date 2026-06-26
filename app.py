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
    get_run_status,
)
from database import (
    init_db,
    insert_deployment,
    get_deployments_by_date,
    update_build_status,
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

# Defaults used by Code Pull flow
environment = None
use_vendor_image = True

# Show only for Collections/Image extraction flow
if extraction_type == "Image Tag Extraction":
    environment = st.selectbox(
        "Environment",
        list(PIPELINE_MAPPING.keys())
    )

    use_vendor_image = st.checkbox(
        "useVendorImage",
        value=True
    )

use_ai_extractor = st.checkbox(
    "Use AI Agent for unstructured documents",
    value=True
)

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
            st.success("Code pull details extracted.")
        except Exception as exc:
            st.error(f"Code pull extraction failed: {str(exc)}")

    if "code_pull_json" in st.session_state:
        st.subheader("Extracted Code Pull JSON")

        json_text = st.text_area(
            "Validate / edit JSON before triggering code-pull pipeline",
            value=json.dumps(st.session_state["code_pull_json"], indent=2),
            height=350,
        )

        try:
            validated_json = json.loads(json_text)
            st.success("JSON is valid")
            st.json(validated_json)

            items = validated_json.get("items", [])

            if not items:
                st.warning("No code-pull items found in JSON.")
            else:
                st.subheader("Code Pull Items")

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

                st.caption(
                    "Code-pull pipeline parameters are APP, PROFINCH_BRANCH, and LIST_ONLY."
                )

                if st.button("Trigger Selected Code Pull Pipelines"):
                    if selected_items_df.empty:
                        st.warning("Select at least one code-pull item.")
                    else:
                        results = []

                        for _, row in selected_items_df.iterrows():
                            item = row.drop(labels=["Select"]).to_dict()

                            try:
                                result = trigger_code_pull_pipeline_item(item)
                                results.append({
                                    "pipeline_application": item.get("pipeline_application"),
                                    "branch_name": item.get("branch_name"),
                                    "status": "triggered",
                                    "run_id": result.get("id"),
                                    "url": result.get("_links", {}).get("web", {}).get("href", ""),
                                    "error": "",
                                })
                            except Exception as exc:
                                results.append({
                                    "pipeline_application": item.get("pipeline_application"),
                                    "branch_name": item.get("branch_name"),
                                    "status": "failed",
                                    "run_id": "",
                                    "url": "",
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

    if not extracted_data:
        st.warning("No image references found in document.")
    else:
        df = pd.DataFrame(extracted_data)

        st.success(f"Found {len(df)} services")

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
            if selected_df.empty:
                st.warning("Select at least one service.")
            else:
                for _, row in selected_df.iterrows():
                    result = trigger_service(
                        service=row["Service"],
                        vendor_image=row["vendorImage"],
                        environment=environment,
                        use_vendor_image=use_vendor_image,
                    )

                    insert_deployment(result)

                st.success("Trigger request completed. Check dashboard below.")


st.divider()
st.subheader("Deployment Dashboard")

selected_date = st.date_input(
    "Select deployment date",
    value=date.today(),
)

if st.button("Refresh Build Status"):
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

    st.success("Build status refreshed.")


dashboard_df = get_deployments_by_date(selected_date)

if dashboard_df.empty:
    st.info("No deployments found for selected date.")
else:
    dashboard_df.insert(0, "retrigger", False)

    edited_dashboard_df = st.data_editor(
        dashboard_df,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "retrigger": st.column_config.CheckboxColumn("Retrigger", default=False)
        },
    )

    if st.button("Retrigger Selected Services"):
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

            st.success("Retrigger completed. Click Refresh Build Status.")

    st.subheader("Build Links")

    build_view = dashboard_df[
        [
            "service_name",
            "run_id",
            "build_state",
            "build_result",
            "build_url",
        ]
    ].dropna(subset=["run_id"])

    if not build_view.empty:
        build_view = build_view.rename(
            columns={
                "service_name": "Service",
                "run_id": "Build Run",
                "build_state": "State",
                "build_result": "Result",
                "build_url": "Link",
            }
        )

        st.dataframe(
            build_view,
            use_container_width=True,
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="Open")
            },
        )
    else:
        st.info("No build links found.")

