.PHONY: ui-install ui-dev ui-run ui-test ui-docker

ui-install:          ## install the web UI dependencies
	pip install -r ui/backend/requirements.txt

ui-dev:              ## run the UI with autoreload on http://localhost:8000
	cd ui/backend && uvicorn main:app --reload --port 8000

ui-run:              ## run the UI without reload
	cd ui/backend && uvicorn main:app --host 0.0.0.0 --port 8000

ui-test:             ## web UI tests (offline)
	pytest ui/tests -q

ui-docker:           ## build and run the container
	docker compose up --build
