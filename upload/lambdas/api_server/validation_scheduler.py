import json
import os
import re
import urllib.parse
import uuid
import boto3

from ...common.uploaded_file import UploadedFile
from ...common.batch import JobDefinition
from ...common.database import create_pg_record, update_pg_record
from ...common.validation_event import UploadedFileValidationEvent

batch = boto3.client('batch')


class ValidationScheduler:

    JOB_NAME_ALLOWABLE_CHARS = '[^\w-]'

    def __init__(self, uploaded_file: UploadedFile):
        self.file = uploaded_file
        self.file_key = self.file.upload_area.uuid + '/' + urllib.parse.quote(self.file.name)

    def schedule_validation(self, validator_docker_image: str, environment: dict) -> str:
        validation_id = str(uuid.uuid4())
        job_defn = self._find_or_create_job_definition_for_image(validator_docker_image)
        environment['DEPLOYMENT_STAGE'] = os.environ['DEPLOYMENT_STAGE']
        environment['INGEST_AMQP_SERVER'] = os.environ['INGEST_AMQP_SERVER']
        environment['INGEST_API_KEY'] = os.environ['INGEST_API_KEY']
        environment['API_HOST'] = os.environ['API_HOST']
        environment['CONTAINER'] = 'DOCKER'
        environment['VALIDATION_ID'] = validation_id
        file_s3loc = "s3://{bucket}/{file_key}".format(
            bucket=self.file.upload_area.bucket_name,
            file_key=self.file_key
        )
        command = ['/validator', file_s3loc]
        self.validation_id = self._enqueue_batch_job(job_defn, command, environment)
        validation_event = UploadedFileValidationEvent(file_id=self.file_key,
                                                       validation_id=validation_id,
                                                       job_id=self.validation_id,
                                                       status="SCHEDULED")
        validation_event.create_record()
        return self.validation_id

    def _find_or_create_job_definition_for_image(self, validator_docker_image):
        job_defn = JobDefinition(docker_image=validator_docker_image, deployment=os.environ['DEPLOYMENT_STAGE'])
        if job_defn.load():
            return job_defn
        else:
            job_defn.create(job_role_arn=os.environ['VALIDATION_JOB_ROLE_ARN'])
        return job_defn

    def _enqueue_batch_job(self, job_defn, command, environment):
        job_name = "-".join(["validation", os.environ['DEPLOYMENT_STAGE'], self.file.upload_area.uuid, self.file.name])
        job_name = re.sub(self.JOB_NAME_ALLOWABLE_CHARS, "", job_name)[0:128]
        job = batch.submit_job(
            jobName=job_name,
            jobQueue=os.environ['VALIDATION_JOB_QUEUE_ARN'],
            jobDefinition=job_defn.arn,
            containerOverrides={
                'command': command,
                'environment': [dict(name=k, value=v) for k, v in environment.items()]
            }
        )
        print(f"Enqueued job {job['jobId']} to validate {self.file.upload_area.uuid}/{self.file.name} "
              f"using job definition {job_defn.arn}:")
        print(json.dumps(job))
        return job['jobId']