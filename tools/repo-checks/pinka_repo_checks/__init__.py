"""Repository quality checks shared by every Pokus component.

The package implements the documentation checks
and the privacy scan:

* ``privacy``: scans files for personal or installation-specific data.
* ``docs-lint``: validates page front matter, code-block conventions and
  ``qa:run`` / ``qa:skip`` markers.
* ``links``: reports broken external links in Markdown files.
* ``mermaid-extract``: writes every Mermaid diagram to its own file so that
  ``mmdc`` can validate the syntax.

The privacy scanner and the link check use the Python standard library only, so
they also run outside the development environment.
"""

__version__ = "0.1.0"
