#!/bin/bash
export ENWIKI_DB=/home/kyle/Documents/projects/local_wikipedia/dumps/enwiki.db
export ENWIKI_RAG_DB=/home/kyle/Documents/projects/local_wikipedia/dumps/enwiki_rag.db
exec uvicorn proxy_server:app --host 0.0.0.0 --port 8000
