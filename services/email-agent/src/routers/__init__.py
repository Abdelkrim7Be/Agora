"""Domain routers for the email-agent API.

`src/api.py` used to hold every route. It now keeps the app itself — lifespan,
middleware, run lifecycle, mailbox connection, sync — and the cohesive route
groups live here, one module per domain.

Import direction is one way: `api.py` imports these modules and calls
`include_router`; a router never imports `api`. Anything two of them need lives
in `src/api_shared.py`.

One consequence worth knowing when writing tests: `from x import y` binds a new
name per module, so a test that monkeypatches `src.api.<name>` no longer reaches
a route that moved here. Patch the router module that binds the name — see
`_patch_categories_path` in `tests/test_control_panel_api.py` for the case where
several modules bind the same constant.
"""
