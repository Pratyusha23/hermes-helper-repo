HERMES_SCRIPTS ?= $(HOME)/.hermes/scripts
FIXTURE        := $(CURDIR)/examples/report.sample.json

.PHONY: help test contract inputs demo check probe probe-lenient install uninstall

help:
	@echo "make test       run every offline test (no network)"
	@echo "make contract   output contract only -- never relax these"
	@echo "make inputs     parser tests against saved fixtures"
	@echo "make demo       show all three output modes, using the fixture"
	@echo "make check      hit the live endpoints once and print the report"
	@echo "make probe      canary: every provider must answer, keeps the bytes"
	@echo "make probe-lenient  passes if each symbol has one live provider"
	@echo "make install    copy the scripts into $(HERMES_SCRIPTS)"
	@echo "make uninstall  remove them again"

test:
	python3 -m unittest discover -s tests -v

# report.json's shape. Yahoo is allowed to change; this is not.
contract:
	python3 -m unittest discover -s tests -p 'test_contract.py' -v

inputs:
	python3 -m unittest discover -s tests -p 'test_quotes.py' -v

# Exercises the formatter and the gate without touching the network.
demo:
	@echo "=== MODE=plain ==="
	@FIXTURE=$(FIXTURE) MODE=plain bash scripts/commodities.sh
	@echo
	@echo "=== MODE=gate, threshold 1.5 (sample biggest_move is 1.92) ==="
	@FIXTURE=$(FIXTURE) MODE=gate MOVE_THRESHOLD=1.5 bash scripts/commodities.sh
	@echo "=== MODE=gate, threshold 3.0 ==="
	@FIXTURE=$(FIXTURE) MODE=gate MOVE_THRESHOLD=3.0 bash scripts/commodities.sh

# Both upstream sources are unofficial. Run this monthly: a silent format
# change is the most likely way the job dies.
check:
	python3 scripts/quotes.py

# Strict on purpose: a working primary does not excuse a broken fallback.
probe:
	python3 scripts/probe.py --out diagnosis

probe-lenient:
	python3 scripts/probe.py --out diagnosis --allow-fallback

install:
	mkdir -p $(HERMES_SCRIPTS)
	install -m 755 scripts/commodities.sh $(HERMES_SCRIPTS)/
	install -m 755 scripts/quotes.py      $(HERMES_SCRIPTS)/
	install -m 644 scripts/format_plain.jq $(HERMES_SCRIPTS)/
	install -m 755 scripts/briefing-plain.sh $(HERMES_SCRIPTS)/
	install -m 755 scripts/briefing-raw.sh   $(HERMES_SCRIPTS)/
	install -m 755 scripts/briefing-gate.sh  $(HERMES_SCRIPTS)/
	install -m 755 scripts/canary_gate.sh    $(HERMES_SCRIPTS)/
	install -m 755 scripts/probe.py          $(HERMES_SCRIPTS)/
	@echo "installed into $(HERMES_SCRIPTS)"

uninstall:
	rm -f $(HERMES_SCRIPTS)/commodities.sh \\
	      $(HERMES_SCRIPTS)/quotes.py \\
	      $(HERMES_SCRIPTS)/format_plain.jq \
	      $(HERMES_SCRIPTS)/briefing-plain.sh \
	      $(HERMES_SCRIPTS)/briefing-raw.sh \
	      $(HERMES_SCRIPTS)/briefing-gate.sh
