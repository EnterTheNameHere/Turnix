# file: backend/content/contentRootCatalog.py
from __future__ import annotations

from dataclasses import dataclass

from backend.content.contentRoot import ContentRoot
from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString


@dataclass(frozen=True)
class ContentRootCatalog:
    """
    Explicit catalog of content roots selected by platform code.

    This is not root discovery, Pack discovery, layer priority, dependency
    solving, version resolution, or Pack loading.
    """

    rootsById: dict[str, ContentRoot]

def createContentRootCatalog(
    *,
    roots: tuple[ContentRoot, ...],
) -> ContentRootCatalog:
    if not isinstance(roots, tuple):
        raise UsageError(f"roots must be a tuple, not {type(roots)}.__name__.")

    rootsById: dict[str, ContentRoot] = {}

    for index, root in enumerate(roots):
        if not isinstance(root, ContentRoot):
            raise UsageError(f"roots[{index}] must be a ContentRoot, not {type(root)}.__name__.")

        if root.rootId in rootsById:
            raise UsageError(f"Duplicate content rootId {root.rootId} in roots.")

        rootsById[root.rootId] = root

    return ContentRootCatalog(rootsById=rootsById)

def getContentRoot(
    *,
    catalog: ContentRootCatalog,
    rootId: str,
) -> ContentRoot:
    if not isinstance(catalog, ContentRootCatalog):
        raise UsageError(f"catalog must be a ContentRootCatalog, not {type(catalog)}.__name__.")

    cleanRootId = requireExactNonBlankString(rootId, "rootId")

    try:
        return catalog.rootsById[cleanRootId]
    except KeyError as err:
        raise UsageError(f"Unknown content rootId: {cleanRootId}.") from err
