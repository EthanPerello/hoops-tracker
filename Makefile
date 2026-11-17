.PHONY: update serve api serve-site test
PORT ?= 8000

update:
	python3 scripts/update.py

serve:
	python3 -m http.server $(PORT)

api:            # build the full static site + agent API into _site/
	python3 scripts/build_api.py _site

serve-site: api
	python3 -m http.server $(PORT) -d _site

test:
	python3 -m unittest discover tests
