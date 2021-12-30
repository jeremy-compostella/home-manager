clean:
	rm -f src/*~ doc/*.md

SRCS=$(wildcard src/*.py)
DOCS=$(addprefix doc/, $(notdir $(SRCS)))

doc/%.md: src/%.py
	pydoc-markdown -m $(notdir $(basename $<)) -I ${PWD}/$(dir $<) > $@

doc: $(DOCS:.py=.md)

install:
ifneq ($(INSTALL_TARGET),)
	scp -r src/ etc $(INSTALL_TARGET)
else
	@echo 'Missing INSTALL_TARGET'
	@echo 'Provide an SSH INSTALL_TARGET: <user>@<host>:<path>'
endif

install_home_assistant:
ifneq ($(INSTALL_TARGET),)
	scp -r home-assistant/home_manager/ $(INSTALL_TARGET)/custom_components
	scp -r home-assistant/*.yaml $(INSTALL_TARGET)/
else
	@echo 'Missing INSTALL_TARGET'
	@echo 'Provide an SSH INSTALL_TARGET: <user>@<host>:<path>'
endif
