import os
import json
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def get_openai_client():
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY environment variable is missing")
    return OpenAI(api_key=OPENAI_API_KEY)


def extract_json_from_ai_response(content):
    content = content.strip()

    if content.startswith("```"):
        content = content.replace("```json", "")
        content = content.replace("```", "")
        content = content.strip()

    start = content.find("{")
    end = content.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("No valid JSON object found in AI response")

    return json.loads(content[start:end + 1])


def ai_extract_services_from_text(document_text):
    client = get_openai_client()

    prompt = f"""
You are an AI extraction agent for deployment release documents.

Extract all container image deployment details from the document text.

Return ONLY valid JSON. No markdown. No explanation.

Required JSON format:
{{
  "services": [
    {{
      "service_display_name": "",
      "service": "",
      "image_registry": "",
      "image_tag": "",
      "vendorImage": ""
    }}
  ]
}}

Rules:
1. Extract only container images related to deployment/release.
2. service must be image name only.
3. image_registry must be registry only.
4. image_tag must be tag only.
5. vendorImage must be service:image_tag.
6. vendorImage must not include registry.
7. If no services are found, return {{"services": []}}.

Document text:
{document_text}
"""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": "You extract structured deployment image data from unstructured release documents."
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
    )

    ai_json = extract_json_from_ai_response(response.choices[0].message.content)

    rows = []
    seen = set()

    for item in ai_json.get("services", []):
        service = str(item.get("service", "")).strip()
        image_tag = str(item.get("image_tag", "")).strip()
        vendor_image = str(item.get("vendorImage", "")).strip()

        if not vendor_image and service and image_tag:
            vendor_image = f"{service}:{image_tag}"

        if service and image_tag and vendor_image and vendor_image not in seen:
            seen.add(vendor_image)
            rows.append({
                "Select": True,
                "Service": service,
                "Image Tag": image_tag,
                "vendorImage": vendor_image,
                "Extraction Method": "AI-Agent",
            })

    return rows


def ai_extract_code_pull_details(document_text):
    client = get_openai_client()

    prompt = f"""
You are an AI agent that extracts code-pull and build-pipeline parameters from banking release documents.

Return ONLY valid JSON. No markdown. No explanation.

Required JSON format:
{{
  "items": [
    {{
      "application_name": "",
      "pipeline_application": "",
      "application_type": "",
      "repository_name": "",
      "branch_name": "",
      "environment": "",
      "code_base_version": "",
      "change_type": "",
      "azure_commit_id": "",
      "list_only": false,
      "build_branch": "",
      "war_files": "",
      "jar_files": "",
      "deploy_type": "Regular",
      "confidence": "",
      "notes": ""
    }}
  ]
}}

Valid pipeline_application values:
- cmncore
- moc
- obdx
- oblm
- oblmic
- obp
- obp-kernel
- obtf
- obtf-kernel
- obtfpm
- obvam
- obvamic
- plato
- r2ppr-obtfpm
- obp-pk
- obp-pk-kernel
- obp-t24
- obp-t24-kernel

General rules:
1. Return one item per Repo Name / Branch Name block.
2. If Repo Name is missing but a clear application and sync branch is present, still create one item.
3. Extract branch_name from Branch Name, Profinch Branch, Deployment Steps, or sync instruction.
4. Extract environment from Environment, build release, deployment target, or deployment steps.
5. Extract azure_commit_id from Azure Commit ID or any 40-character commit SHA.
6. Extract WAR and JAR files from the same application/repository block only.
7. war_files must contain .war files comma-separated, or "None".
8. jar_files must contain .jar files comma-separated, or "None".
9. deploy_type is "Regular" unless document explicitly says "NewSetup".

Application mapping:
- Repo/document contains MOCORE -> pipeline_application "moc".
- Repo/document contains OBTFPM -> pipeline_application "obtfpm".
- Repo/document contains CMNCORE or COMMONCORE -> pipeline_application "cmncore".
- Repo/document contains PLATO -> pipeline_application "plato".
- Repo/document contains OBDX or Oracle Banking Digital Experience -> pipeline_application "obdx".
- Repo/document contains OBTF or Oracle Banking Trade Finance -> pipeline_application "obtf".
- Repo/document contains OBP or Oracle Banking Payment -> pipeline_application "obp".
- Repo/document contains OBLM -> pipeline_application "oblm".
- Repo/document contains OBLMIC or OBLM-IC -> pipeline_application "oblmic".
- Repo/document contains OBVAM -> pipeline_application "obvam".
- Repo/document contains OBVAMIC or OBVAM-IC -> pipeline_application "obvamic".

Kernel/custom override:
- If application is OBTF and text or branch contains KERNEL, MOS, kernel branch, or OBTF Kernel -> pipeline_application "obtf-kernel".
- If application is OBP and text or branch contains KERNEL, MOS, kernel branch, or OBP Kernel -> pipeline_application "obp-kernel".
- If text contains CUSTOMIZATION, Custom, Custom Fix, Mashreq Customizations -> application_type "custom".
- If text contains KERNEL, Kernel Fix, MOS branch -> application_type "kernel".
- If neither is clear -> application_type "standard".

Exact build_branch mapping:
- obtf:
  UAT or R2UAT -> release/uat
  PREPROD or PPR -> release/ppr
  T24 or T24-UAT -> release/t24
  PROD -> release/prod
  OMSIT -> release/omsit

- obtf-kernel:
  T24 or T24-UAT -> release/mos-t24uat
  UAT or R2UAT -> release/mos
  PREPROD or PPR -> release/mos-preprod
  PROD -> release/mos-preprod
  OMSIT -> release/mos

- obp-kernel:
  T24 or T24-UAT -> release/most24
  UAT or R2UAT -> release/mos
  PREPROD or PPR -> release/mos-preprod
  PROD -> release/mos-prod-hk
  OMSIT -> release/mos-omsit

- obp:
  UAT or R2UAT -> release/uat
  PREPROD or PPR -> release/ppr
  T24 or T24-UAT -> release/t24
  PROD -> release/prod
  OMSIT -> release/omsit

- obtfpm, moc, plato, cmncore, obdx, oblm:
  UAT or R2UAT -> release/uat
  PREPROD or PPR -> release/ppr
  T24 or T24-UAT -> release/t24
  OMSIT -> release/omsit
  PROD -> release/prod

- oblmic, obvam, obvamic:
  UAT or R2UAT -> release/uat
  PREPROD or PPR -> release/ppr
  T24 or T24-UAT -> release/t24
  PROD -> release/prod

OBP special:
- If document says Oracle Banking Payment, OBP Release Document, or Services OBP, create OBP item even if Repo Name is missing.
- Extract branch from sentence like:
  DevOps Team will sync the OBP_Kernel_Hotfix_T24_USUKHK from the Profinch to Mashreq Env.
- Expected branch_name = OBP_Kernel_Hotfix_T24_USUKHK.
- If KERNEL is present, use pipeline_application "obp-kernel".

OBDX special:
- If document says OBDX, Oracle Banking Digital Experience, or repo has OBDX, create OBDX item.
- If Branch Name exists, use it.
- If sync instruction exists, extract branch from sync sentence.
- If no WAR/JAR exists, set both to "None".

OBTF special:
- If document says OBTF, Oracle Banking Trade Finance, or repo has OBTF, create OBTF item.
- If KERNEL/MOS is present, use "obtf-kernel".
- If CUSTOMIZATION/Custom Fix is present and kernel is not present, use "obtf".
- Extract all .war and .jar files from the same OBTF block.

OBTFPM/MOC special:
- If Product Name is OBTFPM but repo name is MOCORE_14.7_Mashreq_Customizations, pipeline_application must be "moc".
- If Product Name is OBTFPM and repo name is OBTFPM_14.7_Mashreq_Customizations, pipeline_application must be "obtfpm".
- If Product Name is OBTFPM and repo name is CMNCORE or COMMONCORE, pipeline_application must be "cmncore".
- If Product Name is OBTFPM and repo name is PLATO, pipeline_application must be "plato".
- Return separate items for each Repo Name / Branch Name block.

Confidence:
- high if pipeline_application and branch_name are clearly found.
- medium if one field is inferred.
- low if important values are missing.

If no valid item found, return:
{{"items": []}}

Document text:
{document_text}
"""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": "You extract code-pull and build-pipeline parameters from banking release documents."
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
    )

    return extract_json_from_ai_response(response.choices[0].message.content)
