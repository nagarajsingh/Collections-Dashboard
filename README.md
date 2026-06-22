# Collections Deployment Dashboard

Streamlit dashboard for Collections deployments.

## Features

- Upload release PDF
- Extract service image tags from ACR image locations
- Trigger Azure DevOps service pipelines
- Store deployment history in PostgreSQL
- Refresh build status
- Discover auto-created classic release pipelines
- Show build and release links by deployment date
- Retrigger selected services with `useVendorImage` enabled or disabled

## Required environment variables

```bash
AZDO_ORG=MashreqCorpTech
AZDO_PROJECT=Titan
AZDO_BRANCH=refs/heads/master
AZDO_PAT=<your_pat>
DB_HOST=<postgres_host>
DB_PORT=5432
DB_NAME=deploydb
DB_USER=deployuser
DB_PASSWORD=deploypass
```

## Run PostgreSQL locally without Docker network

```bash
docker run -d \
  --name postgres-deploy \
  -e POSTGRES_DB=deploydb \
  -e POSTGRES_USER=deployuser \
  -e POSTGRES_PASSWORD=deploypass \
  -p 5432:5432 \
  -v postgres_data:/var/lib/postgresql/data \
  postgres:16
```

## Build and run app

Windows/Mac Docker Desktop:

```bash
docker build -t collections-dashboard .

docker run -d \
  --name collections-dashboard \
  -p 8501:8501 \
  -e AZDO_ORG=MashreqCorpTech \
  -e AZDO_PROJECT=Titan \
  -e AZDO_BRANCH=refs/heads/master \
  -e AZDO_PAT=YOUR_PAT_TOKEN \
  -e DB_HOST=host.docker.internal \
  -e DB_PORT=5432 \
  -e DB_NAME=deploydb \
  -e DB_USER=deployuser \
  -e DB_PASSWORD=deploypass \
  collections-dashboard
```

Linux:

```bash
docker run -d \
  --name collections-dashboard \
  --add-host=host.docker.internal:host-gateway \
  -p 8501:8501 \
  -e AZDO_ORG=MashreqCorpTech \
  -e AZDO_PROJECT=Titan \
  -e AZDO_BRANCH=refs/heads/master \
  -e AZDO_PAT=YOUR_PAT_TOKEN \
  -e DB_HOST=host.docker.internal \
  -e DB_PORT=5432 \
  -e DB_NAME=deploydb \
  -e DB_USER=deployuser \
  -e DB_PASSWORD=deploypass \
  collections-dashboard
```

Open:

```text
http://localhost:8501
```

## Notes

Do not commit Azure DevOps PATs or database passwords. Use environment variables or secrets.
