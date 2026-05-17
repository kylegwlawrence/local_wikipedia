"""arXiv metadata harvest, embedding, and retrieval.

Parallel to the wiki abstraction: metadata lives in ``dumps/arxiv.db`` and
embeddings live in ``dumps/arxiv_rag.db``. One chunk per paper, indexed by
title + abstract + categories. Click-through on results links out to
``arxiv.org/abs/{id}`` — there is no local reading view.
"""
