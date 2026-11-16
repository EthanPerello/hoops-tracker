.PHONY: update serve
PORT ?= 8000

update:
	python3 scripts/update.py

serve:
	python3 -m http.server $(PORT)
