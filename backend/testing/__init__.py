"""Shared test helpers.

Lives at the backend root rather than under any one package because the
pipeline, reads, api, mcp_server, and evals suites all seed the same fixture
novel. It previously sat in ``reads/tests/``, which meant every other suite
imported its fixtures out of the read layer's test package.
"""
