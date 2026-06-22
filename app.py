import os
import re
import json
from datetime import date

import pandas as pd
import pdfplumber
import requests
import streamlit as st
from requests.auth import HTTPBasicAuth
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Collections Deployment Dashboard", layout="wide")
st.title("Collections Deployment Dashboard")

ORG = os.getenv("AZDO_ORG")
PROJECT = os.getenv("AZDO_PROJECT")
PAT = os.getenv("AZDO_PAT")
BRANCH = os.getenv("AZDO_BRANCH", "refs/heads/master")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
API_VERSION = "7.1"


def load_pipeline_mapping():
    with open("pipeline_mapping.json", "r", encoding="utf-8") as file:
        return json.load(file)


PIPELINE_MAPPING = load_pipeline_mapping()


def check_config():
    required = {
        "AZDO_ORG": ORG,
        "AZDO_PROJECT": PROJECT,
        "AZDO_PAT": PAT,
        "DB_HOST": DB_HOST,
        "DB_NAME": DB_NAME,
        "DB_USER": DB_USER,
        "DB_PASSWORD": DB_PASSWORD,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        st.error(f"Missing environment variables: {', '.join(missing)}")
        st.stop()


def get_engine():
    return create_engine(
        f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )


def init_db():
    with get_engine().begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS deployments (
                id SERIAL PRIMARY KEY,
                service_name VARCHAR(200),
                vendor_image VARCHAR(500),
                environment VARCHAR(50),
                use_vendor_image BOOLEAN,
                pipeline_name VARCHAR(300),
                pipeline_id INT,
                run_id INT,
                build_state VARCHAR(100),
                build_result VARCHAR(100),
                build_url TEXT,
                release_id INT,
                release_name VARCHAR(200),
                release_status VARCHAR(100),
                release_url TEXT,
                release_environment_status TEXT,
                approval_status TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))
        conn.execute(text("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS release_id INT;"))
        conn.execute(text("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS release_name VARCHAR(200);"))
        conn.execute(text("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS release_status VARCHAR(100);"))
        conn.execute(text("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS release_url TEXT;"))
        conn.execute(text("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS release_environment_status TEXT;"))
        conn.execute(text("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS approval_status TEXT;"))


def insert_deployment(run):
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO deployments (
                service_name, vendor_image, environment, use_vendor_image,
                pipeline_name, pipeline_id, run_id, build_state,
                build_result, build_url, error
            ) VALUES (
                :service_name, :vendor_image, :environment, :use_vendor_image,
                :pipeline_name, :pipeline_id, :run_id, :build_state,
                :build_result, :build_url, :error
            )
        """), {
            "service_name": run.get("Service"),
            "vendor_image": run.get("vendorImage"),
            "environment": run.get("Environment"),
            "use_vendor_image": run.get("useVendorImage"),
            "pipeline_name": run.get("Pipeline Name"),
            "pipeline_id": run.get("Pipeline ID") or None,
            "run_id": run.get("Run ID") or None,
            "build_state": run.get("State"),
            "build_result": run.get("Result"),
            "build_url": run.get("URL"),
            "error": run.get("Error"),
        })


def get_deployments_by_date(selected_date):
    return pd.read_sql(text("""
        SELECT id, service_name, vendor_image, environment, use_vendor_image,
               pipeline_name, pipeline_id, run_id, build_state, build_result,
               build_url, release_id, release_name, release_status, release_url,
               release_environment_status, approval_status, error, created_at, updated_at
        FROM deployments
        WHERE DATE(created_at) = :selected_date
        ORDER BY id DESC
    """), get_engine(), params={"selected_date": selected_date})


def update_build_status(row_id, state, result, url, error):
    with get_engine().begin() as conn:
        conn.execute(text("""
            UPDATE deployments
            SET build_state = :state,
                build_result = :result,
                build_url = :url,
                error = :error,
                updated_at = NOW()
            WHERE id = :id
        """), {"id": int(row_id), "state": state, "result": result, "url": url, "error": error})


def update_release_details(row_id, release):
    environments = release.get("environments", [])
    env_status = ", ".join([f"{env.get('name')}={env.get('status')}" for env in environments])
    with get_engine().begin() as conn:
        conn.execute(text("""
            UPDATE deployments
            SET release_id = :release_id,
                release_name = :release_name,
                release_status = :release_status,
                release_url = :release_url,
                release_environment_status = :release_environment_status,
                updated_at = NOW()
            WHERE id = :id
        """), {
            "id": int(row_id),
            "release_id": release.get("id"),
            "release_name": release.get("name"),
            "release_status": release.get("status"),
            "release_url": release.get("_links", {}).get("web", {}).get("href", ""),
            "release_environment_status": env_status,
        })


def azdo_auth():
    return HTTPBasicAuth("", PAT)


@st.cache_data(ttl=300)
def get_pipeline_cache():
    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/pipelines?api-version={API_VERSION}"
    response = requests.get(url, auth=azdo_auth(), timeout=30)
    response.raise_for_status()
    return {item.get("name"): item.get("id") for item in response.json().get("value", [])}


def resolve_pipeline_id(pipeline_name):
    return get_pipeline_cache().get(pipeline_name)


def trigger_pipeline(pipeline_id, vendor_image, use_vendor_image):
    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/pipelines/{pipeline_id}/runs?api-version={API_VERSION}"
    payload = {
        "resources": {"repositories": {"self": {"refName": BRANCH}}},
        "templateParameters": {
            "vendorImage": vendor_image,
            "useVendorImage": use_vendor_image,
        },
    }
    return requests.post(url, json=payload, auth=azdo_auth(), headers={"Content-Type": "application/json"}, timeout=30)


def get_run_status(pipeline_id, run_id):
    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/pipelines/{pipeline_id}/runs/{run_id}?api-version={API_VERSION}"
    response = requests.get(url, auth=azdo_auth(), timeout=30)
    if response.status_code != 200:
        return {"state": "unknown", "result": "unknown", "url": "", "error": response.text}
    data = response.json()
    return {
        "state": data.get("state", ""),
        "result": data.get("result", ""),
        "url": data.get("_links", {}).get("web", {}).get("href", ""),
        "error": "",
    }


@st.cache_data(ttl=300)
def get_release_definitions():
    url = f"https://vsrm.dev.azure.com/{ORG}/{PROJECT}/_apis/release/definitions?$top=500&api-version={API_VERSION}"
    response = requests.get(url, auth=azdo_auth(), timeout=30)
    if response.status_code != 200:
        return []
    return response.json().get("value", [])


def get_release_definition_id_by_name(pipeline_name):
    definitions = get_release_definitions()
    for definition in definitions:
        if definition.get("name") == pipeline_name:
            return definition.get("id")
    for definition in definitions:
        release_name = definition.get("name", "")
        if release_name.endswith(pipeline_name):
            return definition.get("id")
    for definition in definitions:
        release_name = definition.get("name", "")
        if pipeline_name in release_name:
            return definition.get("id")
    return None


def release_matches_build(release, build_run_id):
    build_run_id = str(build_run_id)
    for artifact in release.get("artifacts", []):
        instance_ref = artifact.get("instanceReference", {})
        definition_ref = artifact.get("definitionReference", {})
        if str(instance_ref.get("id")) == build_run_id:
            return True
        if str(definition_ref.get("version", {}).get("id")) == build_run_id:
            return True
    return False


def get_release_by_build_run(pipeline_name, build_run_id):
    release_definition_id = get_release_definition_id_by_name(pipeline_name)
    if not release_definition_id:
        return None
    url = f"https://vsrm.dev.azure.com/{ORG}/{PROJECT}/_apis/release/releases?definitionId={release_definition_id}&$top=20&queryOrder=descending&api-version={API_VERSION}"
    response = requests.get(url, auth=azdo_auth(), timeout=30)
    if response.status_code != 200:
        return None
    fallback_latest_release = None
    for release in response.json().get("value", []):
        release_id = release.get("id")
        detail_url = f"https://vsrm.dev.azure.com/{ORG}/{PROJECT}/_apis/release/releases/{release_id}?api-version={API_VERSION}"
        detail_response = requests.get(detail_url, auth=azdo_auth(), timeout=30)
        if detail_response.status_code != 200:
            continue
        release_detail = detail_response.json()
        if fallback_latest_release is None:
            fallback_latest_release = release_detail
        if release_matches_build(release_detail, build_run_id):
            return release_detail
    return fallback_latest_release


def extract_services_from_pdf(pdf_file):
    rows = []
    pattern = r"[a-zA-Z0-9\-\.]+\.azurecr\.io\/([a-zA-Z0-9\-]+):([a-zA-Z0-9_\.\-]+)"
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table or []:
                    for cell in row or []:
                        if not cell:
                            continue
                        for service, tag in re.findall(pattern, str(cell)):
                            rows.append({
                                "Select": True,
                                "Service": service,
                                "Image Tag": tag,
                                "vendorImage": f"{service}:{tag}",
                            })
    unique = []
    seen = set()
    for item in rows:
        if item["vendorImage"] not in seen:
            seen.add(item["vendorImage"])
            unique.append(item)
    return unique


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
    response = trigger_pipeline(pipeline_id, vendor_image, use_vendor_image)
    if response.status_code not in [200, 201]:
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
            "Error": response.text,
        }
    data = response.json()
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


check_config()
init_db()

environment = st.selectbox("Environment", list(PIPELINE_MAPPING.keys()))
use_vendor_image = st.checkbox("useVendorImage", value=True)
uploaded_file = st.file_uploader("Upload Release PDF", type=["pdf"])

if uploaded_file:
    extracted_data = extract_services_from_pdf(uploaded_file)
    if not extracted_data:
        st.warning("No image references found in PDF.")
    else:
        df = pd.DataFrame(extracted_data)
        st.success(f"Found {len(df)} services")
        edited_df = st.data_editor(
            df,
            use_container_width=True,
            num_rows="fixed",
            column_config={"Select": st.column_config.CheckboxColumn("Select", default=True)},
        )
        selected_df = edited_df[edited_df["Select"] == True]
        if st.button("Trigger Selected Service Pipelines"):
            if selected_df.empty:
                st.warning("Select at least one service.")
            else:
                for _, row in selected_df.iterrows():
                    result = trigger_service(
                        row["Service"], row["vendorImage"], environment, use_vendor_image
                    )
                    insert_deployment(result)
                st.success("Trigger request completed. Check dashboard below.")

st.divider()
st.subheader("Deployment Dashboard")
selected_date = st.date_input("Select deployment date", value=date.today())

if st.button("Refresh Build + Release Status"):
    dashboard = get_deployments_by_date(selected_date)
    for _, row in dashboard.iterrows():
        if pd.notna(row["pipeline_id"]) and pd.notna(row["run_id"]):
            status = get_run_status(int(row["pipeline_id"]), int(row["run_id"]))
            update_build_status(row["id"], status["state"], status["result"], status["url"] or row["build_url"], status["error"])
            if status["result"] == "succeeded":
                release = get_release_by_build_run(row["pipeline_name"], row["run_id"])
                if release:
                    update_release_details(row["id"], release)
    st.success("Build and release status refreshed.")

dashboard_df = get_deployments_by_date(selected_date)

if dashboard_df.empty:
    st.info("No deployments found for selected date.")
else:
    dashboard_df.insert(0, "retrigger", False)
    edited_dashboard_df = st.data_editor(
        dashboard_df,
        use_container_width=True,
        num_rows="fixed",
        column_config={"retrigger": st.column_config.CheckboxColumn("Retrigger", default=False)},
    )

    if st.button("Retrigger Selected Services"):
        selected_retry_df = edited_dashboard_df[edited_dashboard_df["retrigger"] == True]
        if selected_retry_df.empty:
            st.warning("Select at least one service to retrigger.")
        else:
            for _, row in selected_retry_df.iterrows():
                result = trigger_service(
                    row["service_name"], row["vendor_image"], row["environment"], use_vendor_image
                )
                insert_deployment(result)
            st.success("Retrigger completed. Click Refresh Build + Release Status.")

    st.subheader("Build Details")
    build_view = dashboard_df[["service_name", "run_id", "build_state", "build_result", "build_url"]].dropna(subset=["run_id"])
    if not build_view.empty:
        build_view = build_view.rename(columns={
            "service_name": "Service",
            "run_id": "Build Run",
            "build_state": "State",
            "build_result": "Result",
            "build_url": "Link",
        })
        st.dataframe(
            build_view,
            use_container_width=True,
            column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open")},
        )
    else:
        st.info("No build links found.")

    st.subheader("Release Details")
    release_view = dashboard_df[[
        "service_name", "release_name", "release_status", "release_environment_status", "release_url"
    ]].dropna(subset=["release_name"])
    release_view = release_view.drop_duplicates(subset=["release_name", "release_url"])
    if not release_view.empty:
        release_view = release_view.rename(columns={
            "service_name": "Service",
            "release_name": "Release",
            "release_status": "Status",
            "release_environment_status": "Environment Status",
            "release_url": "Link",
        })
        st.dataframe(
            release_view,
            use_container_width=True,
            column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open")},
        )
    else:
        st.info("No release links found yet.")
