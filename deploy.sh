#!/bin/sh

FUNCTION="bigqueryImport"
PROJECT="my-project-name"
BUCKET="my-bucket-name"

# set the gcloud project
gcloud config set project ${PROJECT}

gcloud functions deploy bigqueryImport \
    --runtime python37 \
    --trigger-resource ${BUCKET} \
    --trigger-event google.storage.object.finalize
