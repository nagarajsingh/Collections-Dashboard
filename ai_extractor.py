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
7. If a common Version Tag is mentioned and images do not show full tag, apply that version tag.
8. If no services are found, return {{"services": []}}.

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
You are an AI agent that extracts code-pull pipeline parameters from banking release documents.

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
      "list_only": false,
      "confidence": "",
      "notes": ""
    }}
  ]
}}

Azure DevOps code-pull pipeline accepts this APP list only:
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

Rules:

1. Multiple repositories/branches may exist in one document.
   Create one JSON item per repository/branch.

2. Extract application_name from Product Name, Project Name, Services, document title, or deployment information.
   Examples:
   OBP, OBTFPM, OBTF, OBDX, OBLM, OBLMIC, OBVAM, OBVAMIC, CMNCORE, MOC, PLATO.

3. Extract branch_name from fields like:
   - Branch Name
   - Profinch Branch
   - Deployment Steps
   - sync branch instruction

4. Extract repository_name from fields like:
   - Repo Name
   - Repository
   - Profinch Repo

5. Extract environment from fields like:
   - Environment
   - build release
   - deployment target
   Examples: UAT, R2UAT, SIT, RLSIT, T24, PROD.

6. Extract code_base_version from:
   - Code Base Version
   - Release version
   - Version

7. application_type:
   - If document mentions CUSTOMIZATION, Custom, Custom fix, or custom branch, use "custom".
   - If document mentions KERNEL or kernel branch, use "kernel".
   - If neither is clear, use "standard".

8. pipeline_application mapping:
   - OBTFPM -> obtfpm
   - OBTF -> obtf
   - OBDX -> obdx
   - OBLM -> oblm
   - OBLMIC -> oblmic
   - OBVAM -> obvam
   - OBVAMIC -> obvamic
   - CMNCORE -> cmncore
   - MOC -> moc
   - PLATO -> plato

9. Special custom/kernel mapping:
   - OBP + custom -> obp
   - OBP + kernel -> obp-kernel
   - OBTF + custom -> obtf
   - OBTF + kernel -> obtf-kernel
   - OBP T24 + custom -> obp-t24
   - OBP T24 + kernel -> obp-t24-kernel
   - OBP PK + custom -> obp-pk
   - OBP PK + kernel -> obp-pk-kernel

10. For OBTFPM documents:
   - If repo name starts with MOCORE or contains MOCORE, use pipeline_application = "moc".
   - If repo name starts with CMNCORE or contains CMNCORE, use pipeline_application = "cmncore".
   - If repo name starts with PLATO or contains PLATO, use pipeline_application = "plato".
   - If repo name starts with OBTFPM or contains OBTFPM, use pipeline_application = "obtfpm".
   - If there are multiple Repo Name / Branch Name blocks, return one item per block.
11. list_only:
   - Always default to false unless document explicitly says list only.

12. confidence:
   - high if pipeline_application and branch_name are clearly found.
   - medium if one field is inferred.
   - low if important fields are missing.

13. notes:
   - Mention any missing, inferred, or ambiguous values.

14. If no valid code-pull item is found, return:
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
                "content": "You extract code-pull pipeline parameters from banking release documents."
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
    )

    return extract_json_from_ai_response(response.choices[0].message.content)
