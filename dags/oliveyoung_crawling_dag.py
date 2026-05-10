import os
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.providers.docker.operators.docker import DockerOperator

ECR_REGISTRY = os.environ.get("ECR_REGISTRY", "")
S3_BUCKET = os.environ.get("OLIVEYOUNG_S3_BUCKET", "")

with DAG(
    dag_id="oliveyoung_crawling",
    schedule="0 2 */3 * *",  # 3일마다 새벽 2시
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["oliveyoung", "crawling"],
) as dag:

    ecr_login = BashOperator(
        task_id="ecr_login",
        bash_command=(
            "aws ecr get-login-password --region ap-northeast-2 "
            "| docker login --username AWS --password-stdin $ECR_REGISTRY"
        ),
    )

    crawl = DockerOperator(
        task_id="crawl",
        image=f"{ECR_REGISTRY}/evr0/oliveyoung-crawling:latest",
        command=f"--s3-bucket {S3_BUCKET}",
        docker_url="unix://var/run/docker.sock",
        network_mode="host",
        auto_remove="success",
        mount_tmp_dir=False,
        mem_limit="4g",
        shm_size=2 * 1024 * 1024 * 1024,  # 2GB — Playwright 필수
        environment={
            "S3_BUCKET": S3_BUCKET,
            "RUN_ID": "{{ ds_nodash }}",
            "AWS_DEFAULT_REGION": "ap-northeast-2",
            "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID", ""),
            "AWS_SECRET_ACCESS_KEY": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        },
        execution_timeout=timedelta(hours=6),
    )

    trigger_etl = TriggerDagRunOperator(
        task_id="trigger_etl",
        trigger_dag_id="oliveyoung_bronze_to_silver",
        wait_for_completion=False,
    )

    ecr_login >> crawl >> trigger_etl
