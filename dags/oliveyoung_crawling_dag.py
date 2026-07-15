import os
import json
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.docker.operators.docker import DockerOperator
from airflow.providers.http.operators.http import SimpleHttpOperator

ECR_REGISTRY = os.environ.get("ECR_REGISTRY", "")
S3_BUCKET = os.environ.get("OLIVEYOUNG_S3_BUCKET", "")

with DAG(
    dag_id="oliveyoung_crawling",
    schedule="0 2 */3 * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=10)},
    tags=["oliveyoung", "crawling"],
) as dag:

    crawl = DockerOperator(
        task_id="crawl",
        image=f"{ECR_REGISTRY}/evr0/oliveyoung-crawling:latest",
        docker_url="unix://var/run/docker.sock",
        network_mode="host",
        auto_remove="success",
        mount_tmp_dir=False,
        force_pull=True,
        mem_limit="4g",
        shm_size=2 * 1024 * 1024 * 1024,
        environment={
            "S3_BUCKET": S3_BUCKET,
            "RUN_ID": "{{ ds_nodash }}",
            "BATCH_DATE": "{{ data_interval_end | ds }}",
            "AWS_DEFAULT_REGION": "ap-northeast-2",
            "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID", ""),
            "AWS_SECRET_ACCESS_KEY": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            "ICEBERG_WAREHOUSE_PATH": os.environ.get("ICEBERG_WAREHOUSE_PATH", "s3://oliveyoung-crawl-data/olive_young_iceberg_metadata/")
        },
        execution_timeout=None,
    )

    trigger_ec2 = SimpleHttpOperator(
        task_id="trigger_ec2_pipeline",
        http_conn_id="ec2_airflow",
        endpoint="/api/v1/dags/oliveyoung_pipeline/dagRuns",
        method="POST",
        data=json.dumps({"conf": {"batch_date": "{{ data_interval_end | ds }}"}}),
        headers={"Content-Type": "application/json"},
        response_check=lambda response: response.status_code == 200,
    )

    crawl >> trigger_ec2
