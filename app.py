#!/usr/bin/env python3
import aws_cdk as cdk
from chlb_stack import CHLBStack
import os

app = cdk.App()

# Provide these as context or environment variables when deploying
account = "231545823700"
key_name =  "Projectdemo"
mongodb_uri =  "mongodb+srv://ruchithpraharshab23_db_user:Ruchith%402005@ccproject.waghehd.mongodb.net/chlb?retryWrites=true&w=majority&appName=CCPROJECT"
instance_type =  "t3.micro"
region =  "us-east-1"

CHLBStack(app, "CHLBStack",
          env=cdk.Environment(region=region, account=account),
          key_name=key_name,
          mongodb_uri=mongodb_uri,
          instance_type=instance_type)

app.synth()
