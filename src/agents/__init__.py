# AI agents of the pipeline. They are invoked from the domain service layer only --
# never from a router (see `src/products/extraction.py`).
#
#   product_extraction/  URL e-commerce -> fiche produit standardisee
#                        (name, description, category, image_url, source_url)
#
# Vendored as-is: no line of `product_extraction/` is edited. Anything that has to
# change goes through its environment variables or its `register_domain()` /
# `register_adapter()` extension points.
