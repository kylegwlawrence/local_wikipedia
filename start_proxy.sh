#!/bin/bash
export ENWIKI_DB=/home/kyle/Documents/projects/local_wikipedia/dumps/enwiki.db
exec uvicorn proxy_server:app --host 0.0.0.0 --port 8000
