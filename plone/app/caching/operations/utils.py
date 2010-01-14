import wsgiref.handlers
import time
import datetime
import logging

from OFS.interfaces import ITraversable

from zope.interface import alsoProvides
from zope.component import queryMultiAdapter
from zope.component import queryUtility

from zope.annotation.interfaces import IAnnotations

from plone.memoize.interfaces import ICacheChooser

from plone.app.caching.interfaces import IRAMCached
from plone.app.caching.interfaces import IETagValue

from z3c.caching.interfaces import ILastModified

from Products.CMFCore.interfaces import IContentish
from Products.CMFCore.interfaces import ISiteRoot

PAGE_CACHE_KEY = 'plone.app.caching.operations.pagecache'
PAGE_CACHE_ANNOTATION_KEY = 'plone.app.caching.operations.pagecache.key'

logger = logging.getLogger('plone.app.caching')

#
# Basic helper functions
# 

def getContext(published, marker=(IContentish, ISiteRoot,)):
    """Given a published object, attempt to look up a context
    
    ``published`` is the object that was published.
    ``marker`` is a marker interface to look for
    
    Returns an item providing ``marker`` or None, if it cannot be found.
    """
    
    if not isinstance(marker, (list, tuple,)):
        marker = (marker,)
    
    def checkType(context):
        for m in marker:
            if m.providedBy(context):
                return True
        return False
    
    while (
        published is not None
        and not checkType(published)
        and hasattr(published, '__parent__',)
    ):
        published = published.__parent__
    
    if not checkType(published):
        return None
    
    return published

def formatDateTime(dt):
    """Format a Python datetime object as an RFC1123 date.
    
    Returns a string.
    """
    
    return wsgiref.handlers.format_date_time(time.mktime(dt.timetuple()))

def safeLastModified(published):
    """Get a last modified date or None
    """
    
    lastModified = ILastModified(published, None)
    if lastModified is None:
        return None
    
    return lastModified()

def getExpiration(maxage):
    """Get an expiration date as a datetime.
    
    ``maxage`` is the maximum age of the item, in seconds.
    """
    
    now = datetime.datetime.now()
    if maxage > 0:
        return now + datetime.timedelta(seconds=maxage)
    else:
        return now - datetime.timedelta(seconds=10*365*24*3600)

def getRAMCache(globalKey=PAGE_CACHE_KEY):
    """Get a RAM cache instance for the given key. The return value is ``None``
    if no RAM cache can be found, or a mapping object supporting at least
    ``__getitem__()``, ``__setitem__()`` and ``get()`` that can be used to get
    or set cache values.
    
    ``key`` is the global cache key, which must be unique site-wide. Most
    commonly, this will be the operation dotted name.
    """
    
    chooser = queryUtility(ICacheChooser)
    if chooser is None:
        return None
    
    return chooser(globalKey)

def getRAMCacheKey(published, request):
    """Calculate the cache key for pages cached in RAM
    """
    
    # XXX: improve
    if hasattr(published, '__parent__'):
        content = published.__parent__
        if ITraversable.providedBy(content):
            return content.absolute_url_path()
    return request['ACTUAL_URL']

def getETag(published, request, keys=(), extraTokens=()):
    """Calculate an ETag.
    
    ``keys`` is a list of types of items to include in the ETag. These must
    match named multi-adapters on (published, request) providing
    ``IETagValue``.
    
    ``extraTokens`` is a list of additional ETag tokens to include, verbatim
    as strings.
    
    All tokens will be concatenated into an ETag string, separated by pipes.
    """
    
    tokens = []
    for key in keys:
        component = queryMultiAdapter((published, request), IETagValue, name=key)
        if component is None:
            logger.warning("Could not find value adapter for ETag component %s", key)
        else:
            value = component()
            if value is not None:
                tokens.append(value)
    
    for token in extraTokens:
        tokens.append(token)
    
    etag = '|' + '|'.join(tokens)
    return etag.replace(',',';')  # commas are bad in etags

#
# Mutator helpers
# 

def doNotCache(published, request, response):
    """Set response headers to ensure that the response is not cached by
    web browsers or caching proxies.
    
    This is an IE-safe operation. Under certain conditions, IE chokes on
    ``no-cache`` and ``no-store`` cache-control tokens so instead we just
    expire immediately and disable validation.
    """
    
    if 'last-modified' in response.headers:
        del response.headers['last-modified']
    
    response.setHeader('Expires', formatDateTime(getExpiration(0)))
    response.setHeader('Cache-Control', 'max-age=0, must-revalidate, private')

def cacheInBrowser(published, request, response, etag=None, lastmodified=None):
    """Set response headers to indicate that browsers should cache the
    response but expire immediately and revalidate the cache on every
    subsequent request.
    
    ``etag`` is a string value indicating an ETag to use.
    ``lastmodified`` is a datetime object
    
    If neither etag nor lastmodified is given then no validation is
    possible and this becomes equivalent to doNotCache()
    """
    
    if etag is not None:
        response.setHeader('ETag', etag)
    if lastmodified is not None:
        response.setHeader('Last-Modified', formatDateTime(lastmodified))
    response.setHeader('Expires', formatDateTime(getExpiration(0)))
    response.setHeader('Cache-Control', 'max-age=0, must-revalidate, private')
    # -> enable 304s

def cacheInProxy(published, request, response, smaxage, lastmodified=None, etag=None, vary=None):
    """Set headers to cache the response in a caching proxy.
    
    ``smaxage`` is the timeout value in seconds.
    ``lastmodified`` is a datetime object for the last modified time
    ``etag`` is an etag string
    ``vary`` is a vary header string
    """
    
    if lastmodified is not None:
        response.setHeader('Last-Modified', formatDateTime(lastmodified))
        # -> enable 304s
    
    if etag is not None:
        response.setHeader('ETag', etag)
        # -> enable 304s
    
    response.setHeader('Expires', formatDateTime(getExpiration(0)))
    response.setHeader('Cache-Control', 'max-age=0, s-maxage=%s, must-revalidate' %smaxage)
    
    if vary is not None:
        response.setHeader('Vary', vary)

def cacheEverywhere(published, request, response, maxage, lastmodified=None, etag=None, vary=None):
    """Set headers to cache the response in the browser and caching proxy if
    applicable.
    
    ``maxage`` is the timeout value in seconds
    ``lastmodified`` is a datetime object for the last modified time
    ``etag`` is an etag string
    ``vary`` is a vary header string
    """
    
    # Slightly misleading name as caching in RAM is not done here
    if lastmodified is not None:
        response.setHeader('Last-Modified', formatDateTime(lastmodified))
        # -> enable 304s
    
    if etag is not None:
        response.setHeader('ETag', etag)
        # -> enable 304s
    
    response.setHeader('Expires', formatDateTime(getExpiration(0)))
    response.setHeader('Cache-Control', 'max-age=%s, must-revalidate, public' %maxage)
    
    if vary is not None:
        response.setHeader('Vary', vary)

def cacheInRAM(published, request, response, key=None, annotationsKey=PAGE_CACHE_ANNOTATION_KEY):
    """Set a flag indicating that the response for the given request
    should be cached in RAM.
    
    This will signal to a transform chain step after the response has been
    generated to store the result in the RAM cache.
    
    To actually use the cached response, you will need to configure the
    'plone.app.caching.operations.pagecache' mutator.
    
    ``key`` is the caching key to use. If not set, it is calculated by
    calling getRAMCacheKey(published, request). Note that this needs to be
    the same key that is used by the interceptor, so passing a custom key
    implies using a custom interceptor as well.
    
    ``annotationsKey`` is the key used by the transform to look up the
    caching key.
    """

    annotations = IAnnotations(request, None)
    if annotations is None:
        return None
    
    if key is None:
        key = getRAMCacheKey(published, request)
    
    annotations[annotationsKey] = key
    alsoProvides(request, IRAMCached)

#
# RAM cache management
# 

def fetchFromRAMCache(published, request, response, key=None, globalKey=PAGE_CACHE_KEY):
    """Return a page cached in RAM, or None if it cannot be found.
    
    ``key`` is the cache key. If not given, it will be calculated by calling
    ``getRAMCacheKey()``
    
    ``globalKey`` is the global cache key. This needs to be the same key
    as the one used to store the data, so changing it assumes using a
    different storage mechanism than the default
    ``plone.app.caching.operations.pagecache` transform chain step.
    """
    
    cache = getRAMCache(globalKey)
    if cache is None:
        return None

    if key is None:
        key = getRAMCacheKey(published, request)
    
    if not key:
        return None
    
    return cache.get(key)

def cachedResponse(published, request, response, cached):
    """Returned a cached page. Modifies the request (status and headers)
    and returns the cached body.
    
    ``cached`` is an object as returned by ``fetchFromRAMCache()`` and stored
    by ``storeResponseInRAMCache()``, i.e. a triple of (status, header, body).
    """
    
    status, headers, body = cached
    response.setStatus(status)

    for k, v in headers.items():
        if k == 'ETag':
            response.setHeader(k, v, literal=1)
        else:
            response.setHeader(k, v)
    
    return body

def storeResponseInRAMCache(published, request, response, result, globalKey=PAGE_CACHE_KEY, annotationsKey=PAGE_CACHE_ANNOTATION_KEY):
    """Store the given response in the RAM cache.
    
    ``result`` should be the response body as a string.

    ``globalKey`` is the global cache key. This needs to be the same key
    as the one used to fetch the data, so changing it assumes using a
    different interceptor than the default
    ``plone.app.caching.operations.pagecache``

    ``annotationsKey`` is the key in annotations on the request from which 
    the caching key should be retrieved. The default is that used by the
    ``cacheInRAM()`` helper function.
    """
    
    annotations = IAnnotations(request, None)
    if annotations is None:
        return
    
    key = annotations.get(annotationsKey)
    if not key:
        return
    
    cache = getRAMCache(globalKey)
    if cache is None:
        return
    
    status = response.getStatus()
    headers = dict(request.response.headers)
    cache[key] = (status, headers, result)
