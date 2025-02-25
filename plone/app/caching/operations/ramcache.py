from plone.app.caching.interfaces import IRAMCached
from plone.app.caching.operations.utils import storeResponseInRAMCache
from plone.transformchain.interfaces import ITransform
from zope.component import adapter
from zope.interface import implementer
from zope.interface import Interface


GLOBAL_KEY = "plone.app.caching.operations.ramcache"


@implementer(ITransform)
@adapter(Interface, Interface)
class Store:
    """Transform chain element which actually saves the page in RAM.

    This is registered for the ``IRAMCached`` request marker, which is set by
    the ``cacheInRAM()`` helper method. Thus, the transform is only used if
    the caching operation requested it.
    """

    order = 90000

    def __init__(self, published, request):
        self.published = published
        self.request = request

    def transformUnicode(self, result, encoding):
        if self.responseIsSuccess() and IRAMCached.providedBy(self.request):
            storeResponseInRAMCache(
                self.request, self.request.response, result.encode(encoding)
            )
        return None

    def transformBytes(self, result, encoding):
        if self.responseIsSuccess() and IRAMCached.providedBy(self.request):
            storeResponseInRAMCache(self.request, self.request.response, result)
        return None

    def transformIterable(self, result, encoding):
        if self.responseIsSuccess() and IRAMCached.providedBy(self.request):
            result = b"".join(result)
            storeResponseInRAMCache(self.request, self.request.response, result)
            # ITransform contract allows to return an "encoded string" aka bytes
            return result
        return None

    def responseIsSuccess(self):
        status = self.request.response.getStatus()
        return status == 200
