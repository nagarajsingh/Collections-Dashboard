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

General extraction rules:

1. Multiple repository blocks may exist. Create one item per Repo Name / Branch Name block.

2. Extract:
   - application_name from Product Name, Project Name, Services, document title.
   - repository_name from Repo Name.
   - branch_name from Branch Name, Profinch Branch, Deployment Steps, or sync instruction.
   - environment from Environment, build release, deployment target, or deployment steps.
   - code_base_version from Code Base Version, Version, or Release version.
   - azure_commit_id from Azure Commit ID or any 40-character commit SHA.
   - change_type from CUSTOMIZATION, Custom, KERNEL, DB, UI, Config, etc.

3. pipeline_application mapping:
   - If repo contains MOCORE, use "moc".
   - If repo contains OBTFPM, use "obtfpm".
   - If repo contains CMNCORE or COMMONCORE, use "cmncore".
   - If repo contains PLATO, use "plato".
   - OBTF custom -> obtf.
   - OBTF kernel -> obtf-kernel.
   - OBP custom -> obp.
   - OBP kernel -> obp-kernel.
   - OBLM -> oblm.
   - OBLMIC or OBLM-IC -> oblmic.
   - OBVAM -> obvam.
   - OBVAMIC or OBVAM-IC -> obvamic.

4. application_type:
   - If CUSTOMIZATION, Custom, Custom fix, use "custom".
   - If KERNEL or Kernel fixes, use "kernel".
   - Otherwise use "standard".

5. build_branch:
   - R2UAT or UAT -> release/uat.
   - T24-UAT or T24 -> release/t24.
   - PREPROD or PPR -> release/ppr.
   - PROD -> release/prod.
   - OMSIT -> release/omsit.
   - SIT -> release/sit.
   - TXCSIT -> release/txcsit.

6. WAR/JAR extraction:
   - Extract WAR files only from the same repository/application block.
   - Extract JAR files only from the same repository/application block.
   - Include filenames ending with .war in war_files.
   - Include filenames ending with .jar in jar_files.
   - If no WAR files for that block, war_files = "None".
   - If no JAR files for that block, jar_files = "None".
   - Separate multiple files with comma and space.
   - Do not mix files from another Repo Name block.

7. deploy_type:
   - Use "Regular" by default.
   - Use "NewSetup" only if document explicitly mentions NewSetup.

OBP special rules:

8. If document contains Project Name as Oracle Banking Payment or Services as OBP, create an item for OBP even if Repo Name is missing.

9. For OBP documents, branch_name may come from Deployment Steps sentence.
   Example:
   "DevOps Team will sync the OBP_Kernel_Hotfix_T24_USUKHK from the Profinch to Mashreq Env."
   Extract:
   branch_name = OBP_Kernel_Hotfix_T24_USUKHK

10. If OBP document mentions KERNEL, Kernel fixes, or OBP_Kernel branch:
    pipeline_application = "obp-kernel"
    application_type = "kernel"
    change_type = "KERNEL"

11. If OBP document mentions Custom or CUSTOMIZATION and does not mention kernel:
    pipeline_application = "obp"
    application_type = "custom"

12. If OBP document mentions T24-UAT or T24 environment:
    build_branch = "release/t24"

13. If OBP document mentions UAT but not T24:
    build_branch = "release/uat"

14. If OBP document has no WAR/JAR files:
    war_files = "None"
    jar_files = "None"

15. For this OBP sample pattern:
    Services: OBP
    Release 5.12.60
    Azure Commit ID: 27d1552723128d6dfac48fa53330116b9e1bb223
    Deployment Steps: sync OBP_Kernel_Hotfix_T24_USUKHK
    Expected:
    application_name = "OBP"
    pipeline_application = "obp-kernel"
    branch_name = "OBP_Kernel_Hotfix_T24_USUKHK"
    build_branch = "release/t24"
    war_files = "None"
    jar_files = "None"

16. confidence:
    - high if pipeline_application and branch_name are clearly found.
    - medium if one field is inferred.
    - low if important values are missing.

17. notes:
    - Mention missing, inferred, or ambiguous values.

18. If no valid item found, return:
    {{"items": []}}
OBDX special rules:

19. If document contains Product Name, Project Name, Services, title, or repo name related to:
    OBDX, Oracle Banking Digital Experience, Digital Experience, obdx
    then create an item for OBDX.

20. If repository_name contains OBDX, OBDX_14, OBDX_14.7, or obdx customization repo:
    pipeline_application = "obdx"

21. If document has Repo Name and Branch Name block for OBDX:
    Extract one item using:
    repository_name = Repo Name value
    branch_name = Branch Name value
    pipeline_application = "obdx"

22. If document has no Repo Name but Deployment Steps mention sync branch for OBDX:
    Extract branch_name from the sync instruction.
    Example:
    "DevOps Team will sync OBDX_Custom_Fix_Branch from Profinch to Mashreq Env"
    branch_name = OBDX_Custom_Fix_Branch

23. If OBDX document mentions CUSTOMIZATION, Custom, Custom fix:
    application_type = "custom"
    change_type = "CUSTOM"

24. If OBDX document has WAR/JAR files:
    Extract them from the same OBDX block.
    war_files = comma-separated .war files
    jar_files = comma-separated .jar files

25. If OBDX document has no WAR/JAR files:
    war_files = "None"
    jar_files = "None"

26. For OBDX, build_branch mapping:
    R2UAT or UAT -> release/uat
    T24-UAT or T24 -> release/t24
    PREPROD or PPR -> release/ppr
    PROD -> release/prod
    OMSIT -> release/omsit
    SIT -> release/sit
    TXCSIT -> release/txcsit
OBTF special rules:

27. If document contains Product Name, Project Name, Services, title, or repository related to:
    OBTF, Oracle Banking Trade Finance, Trade Finance, obtf
    then create an item for OBTF.

28. If repository_name contains:
    OBTF
    OBTF_14
    OBTF_14.7
    OBTF_Mashreq_Customizations
    then pipeline_application = "obtf".

29. If document mentions:
    KERNEL
    Kernel Fix
    OBTF Kernel
    kernel branch
    MOS branch
    then:
        pipeline_application = "obtf-kernel"
        application_type = "kernel"
        change_type = "KERNEL"

30. If document mentions:
    CUSTOMIZATION
    Custom
    Custom Fix
    Mashreq Customizations
    then:
        pipeline_application = "obtf"
        application_type = "custom"
        change_type = "CUSTOM"

31. If document contains multiple Repo Name / Branch Name blocks:
    create one item for each block.

32. If Deployment Steps contain sync instructions such as:
    "DevOps Team will sync OBTF_Kernel_Hotfix from Profinch to Mashreq Env"
    then extract:
        branch_name = OBTF_Kernel_Hotfix

33. If branch name itself contains:
    kernel
    Kernel
    mos
    MOS
    then override:
        pipeline_application = "obtf-kernel"
        application_type = "kernel"

34. WAR/JAR extraction for OBTF:
    - Extract WAR files only from the same OBTF repository block.
    - Extract JAR files only from the same OBTF repository block.
    - Multiple files should be comma separated.
    - If no WAR files exist:
          war_files = "None"
    - If no JAR files exist:
          jar_files = "None"

35. Build branch mapping for OBTF:
    R2UAT or UAT      -> release/uat
    T24 or T24-UAT    -> release/t24
    PREPROD or PPR    -> release/ppr
    PROD              -> release/prod
    OMSIT             -> release/omsit
    SIT               -> release/sit
    TXCSIT            -> release/txcsit

36. Example:

Product Name: OBTF
Environment: R2UAT
Development: CUSTOMIZATION
Repo Name: OBTF_14.7_Mashreq_Customizations
Branch Name: hotfix_branch_14.7.0.1.0
WAR:
    trade-finance-services.war
JAR:
    obtf-extn.jar

Expected:

{
  "application_name": "OBTF",
  "pipeline_application": "obtf",
  "application_type": "custom",
  "repository_name": "OBTF_14.7_Mashreq_Customizations",
  "branch_name": "hotfix_branch_14.7.0.1.0",
  "environment": "R2UAT",
  "build_branch": "release/uat",
  "war_files": "trade-finance-services.war",
  "jar_files": "obtf-extn.jar"
}

37. Example for kernel:

Product Name: OBTF
Development: KERNEL
Branch Name: OBTF_Kernel_Hotfix_T24

Expected:

{
  "pipeline_application": "obtf-kernel",
  "application_type": "kernel",
  "branch_name": "OBTF_Kernel_Hotfix_T24"
}

Build branch mapping:

For OBTF custom / OBTF-R2:
UAT or R2UAT -> release/uat
PREPROD or PPR -> release/ppr
T24 or T24-UAT -> release/t24
PROD -> release/prod
OMSIT -> release/omsit

For OBTF-KERNEL:
T24 or T24-UAT -> release/mos-t24uat
UAT or R2UAT -> release/mos
PREPROD or PPR -> release/mos-preprod
PROD -> release/mos-preprod
OMSIT -> release/mos

For OBP-KERNEL:
T24 or T24-UAT -> release/most24
UAT or R2UAT -> release/mos
PREPROD or PPR -> release/mos-preprod
PROD -> release/mos-prod-hk
OMSIT -> release/mos-omsit

For OBTFPM, MOC, PLATO, CMNCORE/COMMONCORE, OBLM:
UAT or R2UAT -> release/uat
PREPROD or PPR -> release/ppr
T24 or T24-UAT -> release/t24
OMSIT -> release/omsit
PROD -> release/prod

For OBLMIC / OBLM-IC:
UAT or R2UAT -> release/uat
PREPROD or PPR -> release/ppr
T24 or T24-UAT -> release/t24
PROD -> release/prod

For OBVAM:
UAT or R2UAT -> release/uat
PREPROD or PPR -> release/ppr
T24 or T24-UAT -> release/t24
PROD -> release/prod

For OBVAMIC / OBVAM-IC:
UAT or R2UAT -> release/uat
PREPROD or PPR -> release/ppr
T24 or T24-UAT -> release/t24
PROD -> release/prod
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
