.PHONY: install dev run docker test clean

install:            ## install backend deps into the current environment
	pip install -r backend/requirements.txt

dev:                ## run with autoreload (local development)
	cd backend && uvicorn main:app --reload --port 8000

run:                ## run without reload (production-style, local)
	cd backend && uvicorn main:app --host 0.0.0.0 --port 8000

docker:             ## build and run the container
	docker compose up --build

test:               ## run the backend smoke test
	cd backend && python -m pytest ../tests -q

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
