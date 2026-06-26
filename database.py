import os
import json

import pandas as pd
from sqlalchemy import create_engine, text


DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")


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
                error TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS code_pull_runs (
                id SERIAL PRIMARY KEY,
                pipeline_application VARCHAR(100),
                branch_name VARCHAR(500),
                environment VARCHAR(100),
                build_branch VARCHAR(200),
                war_files TEXT,
                jar_files TEXT,
                deploy_type VARCHAR(100),
                pipeline_id INT,
                run_id INT,
                build_number VARCHAR(200),
                source_branch TEXT,
                status VARCHAR(100),
                result VARCHAR(100),
                run_url TEXT,
                extracted_json JSONB,
                repo_name VARCHAR(300),
                pr_id INT,
                pr_url TEXT,
                pr_status VARCHAR(100),
                pr_review_status VARCHAR(100),
                pr_target_branch TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS build_runs (
                id SERIAL PRIMARY KEY,
                code_pull_id INT,
                pipeline_application VARCHAR(100),
                build_branch VARCHAR(200),
                war_files TEXT,
                jar_files TEXT,
                deploy_type VARCHAR(100),
                pipeline_id INT,
                run_id INT,
                status VARCHAR(100),
                result VARCHAR(100),
                run_url TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """))

        for ddl in [
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS build_branch VARCHAR(200);",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS war_files TEXT;",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS jar_files TEXT;",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS deploy_type VARCHAR(100);",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS repo_name VARCHAR(300);",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS pr_id INT;",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS pr_url TEXT;",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS pr_status VARCHAR(100);",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS pr_review_status VARCHAR(100);",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS pr_target_branch TEXT;",
            "ALTER TABLE code_pull_runs ADD COLUMN IF NOT EXISTS error TEXT;",
        ]:
            conn.execute(text(ddl))


def insert_deployment(run):
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO deployments (
                service_name, vendor_image, environment, use_vendor_image,
                pipeline_name, pipeline_id, run_id, build_state,
                build_result, build_url, error
            )
            VALUES (
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
    return pd.read_sql(
        text("""
            SELECT
                id,
                DATE(created_at) AS activity_date,
                service_name,
                vendor_image,
                environment,
                pipeline_name,
                run_id,
                build_state,
                build_result,
                build_url,
                error,
                created_at,
                updated_at
            FROM deployments
            WHERE DATE(created_at) = :selected_date
            ORDER BY created_at DESC
        """),
        get_engine(),
        params={"selected_date": selected_date},
    )


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
        """), {
            "id": int(row_id),
            "state": state,
            "result": result,
            "url": url,
            "error": error,
        })


def insert_code_pull_run(item, pipeline_id, result, source_branch, extracted_json):
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO code_pull_runs (
                pipeline_application,
                branch_name,
                environment,
                build_branch,
                war_files,
                jar_files,
                deploy_type,
                pipeline_id,
                run_id,
                build_number,
                source_branch,
                status,
                result,
                run_url,
                extracted_json,
                error
            )
            VALUES (
                :pipeline_application,
                :branch_name,
                :environment,
                :build_branch,
                :war_files,
                :jar_files,
                :deploy_type,
                :pipeline_id,
                :run_id,
                :build_number,
                :source_branch,
                :status,
                :result,
                :run_url,
                CAST(:extracted_json AS JSONB),
                :error
            )
        """), {
            "pipeline_application": item.get("pipeline_application"),
            "branch_name": item.get("branch_name"),
            "environment": item.get("environment"),
            "build_branch": item.get("build_branch"),
            "war_files": item.get("war_files") or "None",
            "jar_files": item.get("jar_files") or "None",
            "deploy_type": item.get("deploy_type") or "Regular",
            "pipeline_id": pipeline_id,
            "run_id": result.get("id"),
            "build_number": result.get("name"),
            "source_branch": source_branch,
            "status": result.get("state", "inProgress"),
            "result": result.get("result", ""),
            "run_url": result.get("_links", {}).get("web", {}).get("href", ""),
            "extracted_json": json.dumps(extracted_json),
            "error": "",
        })


def get_code_pull_runs_by_date(selected_date):
    return pd.read_sql(
        text("""
            SELECT
                id,
                DATE(created_at) AS activity_date,
                pipeline_application,
                branch_name,
                environment,
                build_branch,
                war_files,
                jar_files,
                deploy_type,
                pipeline_id,
                run_id,
                build_number,
                source_branch,
                status,
                result,
                run_url,
                repo_name,
                pr_id,
                pr_status,
                pr_review_status,
                pr_target_branch,
                pr_url,
                error,
                created_at,
                updated_at
            FROM code_pull_runs
            WHERE DATE(created_at) = :selected_date
            ORDER BY created_at DESC
        """),
        get_engine(),
        params={"selected_date": selected_date},
    )


def update_code_pull_status(row_id, status, result, run_url, error):
    with get_engine().begin() as conn:
        conn.execute(text("""
            UPDATE code_pull_runs
            SET status = :status,
                result = :result,
                run_url = :run_url,
                error = :error,
                updated_at = NOW()
            WHERE id = :id
        """), {
            "id": int(row_id),
            "status": status,
            "result": result,
            "run_url": run_url,
            "error": error,
        })


def update_code_pull_pr(
    row_id,
    pr_url,
    pr_status,
    pr_review_status,
    error,
    pr_id=None,
    repo_name=None,
    pr_target_branch=None,
):
    with get_engine().begin() as conn:
        conn.execute(text("""
            UPDATE code_pull_runs
            SET pr_id = COALESCE(:pr_id, pr_id),
                repo_name = COALESCE(:repo_name, repo_name),
                pr_url = COALESCE(:pr_url, pr_url),
                pr_status = :pr_status,
                pr_review_status = :pr_review_status,
                pr_target_branch = COALESCE(:pr_target_branch, pr_target_branch),
                error = :error,
                updated_at = NOW()
            WHERE id = :id
        """), {
            "id": int(row_id),
            "pr_id": pr_id,
            "repo_name": repo_name,
            "pr_url": pr_url,
            "pr_status": pr_status,
            "pr_review_status": pr_review_status,
            "pr_target_branch": pr_target_branch,
            "error": error,
        })


def insert_build_run(code_pull_row, pipeline_id, result, normalized_war_files, normalized_jar_files):
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO build_runs (
                code_pull_id,
                pipeline_application,
                build_branch,
                war_files,
                jar_files,
                deploy_type,
                pipeline_id,
                run_id,
                status,
                result,
                run_url,
                error
            )
            VALUES (
                :code_pull_id,
                :pipeline_application,
                :build_branch,
                :war_files,
                :jar_files,
                :deploy_type,
                :pipeline_id,
                :run_id,
                :status,
                :result,
                :run_url,
                :error
            )
        """), {
            "code_pull_id": int(code_pull_row.get("id")),
            "pipeline_application": code_pull_row.get("pipeline_application"),
            "build_branch": code_pull_row.get("build_branch"),
            "war_files": normalized_war_files,
            "jar_files": normalized_jar_files,
            "deploy_type": code_pull_row.get("deploy_type") or "Regular",
            "pipeline_id": pipeline_id,
            "run_id": result.get("id"),
            "status": result.get("state", "inProgress"),
            "result": result.get("result", ""),
            "run_url": result.get("_links", {}).get("web", {}).get("href", ""),
            "error": "",
        })


def get_build_runs_by_date(selected_date):
    return pd.read_sql(
        text("""
            SELECT
                id,
                DATE(created_at) AS activity_date,
                code_pull_id,
                pipeline_application,
                build_branch,
                war_files,
                jar_files,
                deploy_type,
                pipeline_id,
                run_id,
                status,
                result,
                run_url,
                error,
                created_at,
                updated_at
            FROM build_runs
            WHERE DATE(created_at) = :selected_date
            ORDER BY created_at DESC
        """),
        get_engine(),
        params={"selected_date": selected_date},
    )


def update_build_run_status(row_id, status, result, run_url, error):
    with get_engine().begin() as conn:
        conn.execute(text("""
            UPDATE build_runs
            SET status = :status,
                result = :result,
                run_url = :run_url,
                error = :error,
                updated_at = NOW()
            WHERE id = :id
        """), {
            "id": int(row_id),
            "status": status,
            "result": result,
            "run_url": run_url,
            "error": error,
        })
