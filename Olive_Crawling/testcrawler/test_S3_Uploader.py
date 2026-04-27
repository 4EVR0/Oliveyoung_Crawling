from botocore.exceptions import ClientError

from storage.S3_Uploader import S3Uploader


class FakeS3Client:
    def __init__(self):
        self.put_calls = []
        self.part_put_attempts = 0

    def get_object(self, Bucket, Key):
        raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")

    def head_object(self, Bucket, Key):
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    def put_object(self, Bucket, Key, Body, ContentType):
        if "/part_" in Key:
            self.part_put_attempts += 1
            if self.part_put_attempts == 1:
                raise Exception("temporary s3 failure")
        self.put_calls.append(Key)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def test_s3_uploader_retries_part_upload(monkeypatch):
    fake_client = FakeS3Client()

    monkeypatch.setattr("storage.S3_Uploader.boto3.client", lambda _: fake_client)
    monkeypatch.setattr("storage.S3_Uploader.time.sleep", lambda *_: None)

    uploader = S3Uploader(bucket="unit-test-bucket", run_id="20260427_120000")
    uploader.add_products("스킨케어", "크림", [{"name": "A"}])
    uploader.flush_subcategory("스킨케어", "크림")

    assert fake_client.part_put_attempts == 2
    assert uploader._manifest["total_products"] == 1
    assert len(uploader._manifest["parts"]) == 1
