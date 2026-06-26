import os
import requests
from requests.auth import HTTPBasicAuth

ORG = os.getenv("AZDO_ORG")
PROJECT = os.getenv("AZDO_PROJECT")
PAT = os.getenv("AZDO_PAT")
BRANCH = os.getenv("AZDO_BRANCH", "refs/heads/master")
API_VERSION = "7.1"

CODE_PULL_PIPELINE_NAME = os.getenv("CODE_PULL_PIPELINE_NAME")
CODE_PULL_PIPELINE_ID = os.getenv("CODE_PULL_PIPELINE_ID")

CODE_PULL_PARAM_APPLICATION = os.getenv("CODE_PULL_PARAM_APPLICATION", "APP")
CODE_PULL_PARAM_BRANCH = os.getenv("CODE_PULL_PARAM_BRANCH", "PROFINCH_BRANCH")
CODE_PULL_PARAM_LIST_ONLY = os.getenv("CODE_PULL_PARAM_LIST_ONLY", "LIST_ONLY")


def azdo_auth():
    return HTTPBasicAuth("", PAT)


def list_pipelines():
    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/pipelines?api-version={API_VERSION}"
    response = requests.get(url, auth=azdo_auth(), timeout=30)
    response.raise_for_status()
    return response.json().get("value", [])


def resolve_pipeline_id(pipeline_name):
    for pipeline in list_pipelines():
        if pipeline.get("name") == pipeline_name:
            return pipeline.get("id")
    return None


def post_pipeline_run(pipeline_id, template_parameters, ref_name=None):
    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/pipelines/{pipeline_id}/runs?api-version={API_VERSION}"

    payload = {
        "resources": {
            "repositories": {
                "self": {
                    "refName": ref_name or BRANCH
                }
            }
        },
        "templateParameters": template_parameters,
    }

    response = requests.post(
        url,
        json=payload,
        auth=azdo_auth(),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if response.status_code not in [200, 201]:
        raise Exception(response.text)

    return response.json()


def normalize_artifact_names(value):
    if not value:
        return "None"

    value = str(value).strip()

    if value.lower() in ["none", "no", "na", "n/a", ""]:
        return "None"

    normalized = (
        value.replace(";", ",")
        .replace(":", ",")
        .replace("\n", ",")
        .replace("\t", ",")
    )

    parts = []

    for item in normalized.split(","):
        item = item.strip()

        if not item:
            continue

        item = item.replace(".war", "").replace(".jar", "")
        item = item.replace(".WAR", "").replace(".JAR", "")
        item = item.strip()

        if item and item.lower() not in ["none", "no", "na", "n/a"]:
            parts.append(item)

    if not parts:
        return "None"

    return " ".join(parts)


def trigger_image_pipeline(pipeline_id, vendor_image, use_vendor_image):
    return post_pipeline_run(
        pipeline_id,
        {
            "vendorImage": vendor_image,
            "useVendorImage": use_vendor_image,
        }
    )


def trigger_code_pull_pipeline_item(item):
    pipeline_id = CODE_PULL_PIPELINE_ID

    if not pipeline_id:
        if not CODE_PULL_PIPELINE_NAME:
            raise Exception("Either CODE_PULL_PIPELINE_ID or CODE_PULL_PIPELINE_NAME must be configured")
        pipeline_id = resolve_pipeline_id(CODE_PULL_PIPELINE_NAME)

    if not pipeline_id:
        raise Exception(f"Could not resolve code-pull pipeline: {CODE_PULL_PIPELINE_NAME}")

    app = item.get("pipeline_application", "")
    branch = item.get("branch_name", "")
    list_only = item.get("list_only", False)

    if not app:
        raise Exception("Missing pipeline_application")

    if not branch:
        raise Exception("Missing branch_name")

    result = post_pipeline_run(
        pipeline_id,
        {
            CODE_PULL_PARAM_APPLICATION: app,
            CODE_PULL_PARAM_BRANCH: branch,
            CODE_PULL_PARAM_LIST_ONLY: list_only,
        }
    )

    return int(pipeline_id), result


def trigger_build_pipeline_item(item, build_config):
    pipeline_id = build_config.get("pipeline_id")

    if pipeline_id:
        pipeline_id = int(pipeline_id)
    else:
        pipeline_name = build_config.get("pipeline_name")
        if not pipeline_name:
            raise Exception("Build pipeline config must contain pipeline_id or pipeline_name")

        pipeline_id = resolve_pipeline_id(pipeline_name)

    if not pipeline_id:
        raise Exception(f"Could not resolve build pipeline: {build_config.get('pipeline_name')}")

    params = build_config.get("parameters", {})

    branch_param = params.get("branch", "branch")
    war_param = params.get("war_files", "war_files")
    jar_param = params.get("jar_files", "jar_files")
    deploy_type_param = params.get("deploy_type", "deploy_type")

    build_branch = item.get("build_branch") or "release/uat"
    war_files = normalize_artifact_names(item.get("war_files"))
    jar_files = normalize_artifact_names(item.get("jar_files"))
    deploy_type = item.get("deploy_type") or "Regular"

    template_parameters = {
        branch_param: build_branch,
        war_param: war_files,
        jar_param: jar_files,
        deploy_type_param: deploy_type,
    }

    result = post_pipeline_run(
        pipeline_id,
        template_parameters,
        ref_name=build_config.get("pipeline_version_ref") or BRANCH,
    )

    return int(pipeline_id), result, template_parameters


def get_run_status(pipeline_id, run_id):
    url = f"https://dev.azure.com/{ORG}/{PROJECT}/_apis/pipelines/{pipeline_id}/runs/{run_id}?api-version={API_VERSION}"

    response = requests.get(url, auth=azdo_auth(), timeout=30)

    if response.status_code != 200:
        return {
            "state": "unknown",
            "result": "unknown",
            "url": "",
            "error": response.text,
        }

    data = response.json()

    return {
        "state": data.get("state", ""),
        "result": data.get("result", ""),
        "url": data.get("_links", {}).get("web", {}).get("href", ""),
        "error": "",
    }


def build_created_branch_name(profinch_branch, build_number, app_name):
    return f"profinch/{profinch_branch}-{build_number}-{app_name}"


def create_pull_request(repo_name, source_branch, target_branch, title, description):
    url = (
        f"https://dev.azure.com/{ORG}/{PROJECT}"
        f"/_apis/git/repositories/{repo_name}/pullrequests"
        f"?api-version={API_VERSION}"
    )

    payload = {
        "sourceRefName": f"refs/heads/{source_branch}",
        "targetRefName": f"refs/heads/{target_branch}",
        "title": title,
        "description": description,
    }

    response = requests.post(
        url,
        json=payload,
        auth=azdo_auth(),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if response.status_code not in [200, 201]:
        raise Exception(response.text)

    return response.json()


def get_pull_request_details(repo_name, pr_id):
    url = (
        f"https://dev.azure.com/{ORG}/{PROJECT}"
        f"/_apis/git/repositories/{repo_name}/pullrequests/{pr_id}"
        f"?api-version={API_VERSION}"
    )

    response = requests.get(url, auth=azdo_auth(), timeout=30)

    if response.status_code != 200:
        return {
            "status": "unknown",
            "review_status": "unknown",
            "url": "",
            "error": response.text,
        }

    data = response.json()
    reviewers = data.get("reviewers", [])
    votes = [reviewer.get("vote", 0) for reviewer in reviewers]

    if any(vote <= -10 for vote in votes):
        review_status = "rejected"
    elif any(vote > 0 for vote in votes):
        review_status = "approved"
    else:
        review_status = "pending"

    return {
        "status": data.get("status", ""),
        "review_status": review_status,
        "url": data.get("_links", {}).get("web", {}).get("href", ""),
        "error": "",
    }
