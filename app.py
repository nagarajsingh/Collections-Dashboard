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
    build_created_branch_name,
    create_pull_request,
    get_pull_request_details,
    normalize_artifact_names,
)
from database import (
    init_db,
    insert_deployment,
    get_deployments_by_date,
    update_build_status,
    insert_code_pull_run,
    get_code_pull_runs_by_date,
    update_code_pull_status,
    update_code_pull_pr,
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

try:
    with open("repo_mapping.json", "r", encoding="utf-8") as f:
        REPO_MAPPING = json.load(f)
except FileNotFoundError:
    REPO_MAPPING = {}

try:
    with open("pr_branch_mapping.json", "r", encoding="utf-8") as f:
        PR_BRANCH_MAPPING = json.load(f)
except FileNotFoundError:
    PR_BRANCH_MAPPING = {}


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


def normalize_env(env):
    env = str(env or "").strip().lower()

    aliases = {
        "r2uat": "uat",
        "uat": "uat",
        "preprod": "ppr",
        "ppr": "ppr",
        "t24": "t24",
        "prod": "prod",
        "production": "prod",
        "omsit": "omsit",
        "sit": "sit",
        "txcsit": "txcsit",
    }

    return aliases.get(env, env)


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


def refresh_pr_status(selected_date):
    df = get_code_pull_runs_by_date(selected_date)

    for _, row in df.iterrows():
        pr_id = row.get("pr_id")
        repo_name = row.get("repo_name")

        if pd.isna(pr_id) or not repo_name:
            continue

        details = get_pull_request_details(repo_name, int(pr_id))

        update_code_pull_pr(
            row_id=row["id"],
            pr_url=details["url"] or row.get("pr_url", ""),
            pr_status=details["status"],
            pr_review_status=details["review_status"],
            error=details["error"],
            pr_id=int(pr_id),
            repo_name=repo_name,
            pr_target_branch=row.get("pr_target_branch"),
        )


def render_gtb_dashboard(selected_date):
    st.subheader("GTB Application Deployment")

    if st.button("Get / Refresh Code Pull, PR and Build Status"):
        refresh_code_pull_status(selected_date)
        refresh_pr_status(selected_date)
        refresh_build_status(selected_date)
        st.success("GTB status refreshed.")

    code_pull_df = get_code_pull_runs_by_date(selected_date)
    build_df = get_build_runs_by_date(selected_date)

    st.markdown("### Code Pull Status")

    if code_pull_df.empty:
        st.info("No code-pull runs found for selected date.")
    else:
        code_pull_cols = [
            "activity_date",
            "id",
            "pipeline_application",
            "branch_name",
            "environment",
            "result",
            "run_url",
        ]
        st.dataframe(
            code_pull_df[[c for c in code_pull_cols if c in code_pull_df.columns]],
            use_container_width=True,
            column_config={
                "run_url": st.column_config.LinkColumn("Code Pull", display_text="Open"),
            },
        )

        with st.expander("Show code-pull artifact details"):
            artifact_cols = [
                "id",
                "pipeline_application",
                "build_branch",
                "war_files",
                "jar_files",
                "deploy_type",
                "source_branch",
                "created_at",
                "updated_at",
                "error",
            ]
            st.dataframe(
                code_pull_df[[c for c in artifact_cols if c in code_pull_df.columns]],
                use_container_width=True,
            )

    st.markdown("### PR Status")

    if code_pull_df.empty:
        st.info("No PR activity found for selected date.")
    else:
        pr_cols = [
            "activity_date",
            "id",
            "pipeline_application",
            "repo_name",
            "pr_id",
            "pr_status",
            "pr_review_status",
            "pr_target_branch",
            "pr_url",
        ]
        st.dataframe(
            code_pull_df[[c for c in pr_cols if c in code_pull_df.columns]],
            use_container_width=True,
            column_config={
                "pr_url": st.column_config.LinkColumn("PR", display_text="Open"),
            },
        )

    ready_for_pr = code_pull_df[
        (code_pull_df["result"] == "succeeded")
        & (
            code_pull_df["pr_id"].isna()
            | code_pull_df["pr_url"].isna()
            | (code_pull_df["pr_url"] == "")
            | (code_pull_df["pr_status"] == "failed")
        )
    ] if not code_pull_df.empty else pd.DataFrame()

    if not ready_for_pr.empty:
        st.markdown("### Raise PR")

        pr_input_df = ready_for_pr.copy()
        pr_input_df = pr_input_df.sort_values("created_at", ascending=False).reset_index(drop=True)
        pr_input_df.insert(0, "Raise PR", False)
        pr_input_df.loc[0, "Raise PR"] = True

        edited_pr_df = st.data_editor(
            pr_input_df,
            use_container_width=True,
            num_rows="fixed",
            column_config={
                "Raise PR": st.column_config.CheckboxColumn("Raise PR", default=False)
            },
        )

        selected_pr_df = edited_pr_df[edited_pr_df["Raise PR"] == True]

        if st.button("Raise PR for Selected"):
            pr_results = []

            for _, row in selected_pr_df.iterrows():
                row_id = row["id"]
                app = str(row.get("pipeline_application", "")).strip()
                env = normalize_env(row.get("environment", ""))
                source_branch = str(row.get("source_branch", "")).strip()
                branch_name = str(row.get("branch_name", "")).strip()

                repo_name = REPO_MAPPING.get(app)
                target_branch = PR_BRANCH_MAPPING.get(app, {}).get(env)

                if not repo_name:
                    error = f"No repo mapping found for app: {app}"
                    update_code_pull_pr(row_id, "", "failed", "failed", error)
                    pr_results.append({"app": app, "status": "failed", "error": error})
                    continue

                if not target_branch:
                    error = f"No target branch mapping found for app={app}, env={env}"
                    update_code_pull_pr(row_id, "", "failed", "failed", error, repo_name=repo_name)
                    pr_results.append({"app": app, "status": "failed", "error": error})
                    continue

                if not source_branch:
                    error = "Source branch is empty."
                    update_code_pull_pr(
                        row_id,
                        "",
                        "failed",
                        "failed",
                        error,
                        repo_name=repo_name,
                        pr_target_branch=target_branch,
                    )
                    pr_results.append({"app": app, "status": "failed", "error": error})
                    continue

                try:
                    title = f"Code Pull: {app} - {branch_name}"
                    description = (
                        "Auto-created PR from AI Agent document extractor.\n\n"
                        f"Application: {app}\n"
                        f"Environment: {env}\n"
                        f"Source Branch: {source_branch}\n"
                        f"Target Branch: {target_branch}\n"
                        f"Code Pull Run: {row.get('run_id')}\n"
                    )

                    pr = create_pull_request(
                        repo_name=repo_name,
                        source_branch=source_branch,
                        target_branch=target_branch,
                        title=title,
                        description=description,
                    )

                    pr_url = pr.get("_links", {}).get("web", {}).get("href", "")
                    pr_status = pr.get("status", "active")
                    pr_id = pr.get("pullRequestId")

                    update_code_pull_pr(
                        row_id=row_id,
                        pr_url=pr_url,
                        pr_status=pr_status,
                        pr_review_status="pending",
                        error="",
                        pr_id=pr_id,
                        repo_name=repo_name,
                        pr_target_branch=target_branch,
                    )

                    pr_results.append({
                        "app": app,
                        "repo": repo_name,
                        "target_branch": target_branch,
                        "status": pr_status,
                        "pr_id": pr_id,
                        "url": pr_url,
                        "error": "",
                    })

                except Exception as exc:
                    error = str(exc)
                    update_code_pull_pr(
                        row_id=row_id,
                        pr_url="",
                        pr_status="failed",
                        pr_review_status="failed",
                        error=error,
                        repo_name=repo_name,
                        pr_target_branch=target_branch,
                    )
                    pr_results.append({"app": app, "status": "failed", "error": error})

            st.dataframe(pd.DataFrame(pr_results), use_container_width=True)

    st.markdown("### Build Status")

    if build_df.empty:
        st.info("No build runs found for selected date.")
    else:
        build_cols = [
            "activity_date",
            "id",
            "code_pull_id",
            "pipeline_application",
            "build_branch",
            "war_files",
            "jar_files",
            "deploy_type",
            "result",
            "run_url",
            "error",
        ]
        st.dataframe(
            build_df[[c for c in build_cols if c in build_df.columns]],
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

    build_input_cols = [
        "id",
        "pipeline_application",
        "build_branch",
        "war_files",
        "jar_files",
        "deploy_type",
        "result",
    ]

    build_input_df = ready_for_build[[c for c in build_input_cols if c in ready_for_build.columns]].copy()
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
            code_pull_row = code_pull_df[code_pull_df["id"] == row["id"]].iloc[0]
            app = code_pull_row.get("pipeline_application")
            build_config = BUILD_PIPELINE_MAPPING.get(app)

            if not build_config:
                results.append({
                    "app": app,
                    "status": "failed",
                    "error": f"No build pipeline mapping found for app: {app}",
                })
                continue

            item = {
                "build_branch": code_pull_row.get("build_branch"),
                "war_files": code_pull_row.get("war_files") or "None",
                "jar_files": code_pull_row.get("jar_files") or "None",
                "deploy_type": code_pull_row.get("deploy_type") or "Regular",
            }

            try:
                pipeline_id, result, template_parameters = trigger_build_pipeline_item(item, build_config)

                insert_build_run(
                    code_pull_row=code_pull_row,
                    pipeline_id=pipeline_id,
                    result=result,
                    normalized_war_files=template_parameters.get("war_files", "None"),
                    normalized_jar_files=template_parameters.get("jar_files", "None"),
                )

                results.append({
                    "app": app,
                    "branch": template_parameters.get("branch"),
                    "war_files": template_parameters.get("war_files"),
                    "jar_files": template_parameters.get("jar_files"),
                    "deploy_type": template_parameters.get("deploy_type"),
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
