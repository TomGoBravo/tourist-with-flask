'''
WikiLinks Extension for tourist-with-flask
==========================================

Converts [[WikiLinks]] to links with text fetched from sqlalchemy.

Written by Tom Brown, based heavily on WikiLinks Extension that is Copyright The Python Markdown
Project and used original code Copyright [Waylan Limberg](http://achinghead.com/).

License: [BSD](http://www.opensource.org/licenses/bsd-license.php)
'''
from markdown import Extension
from markdown.inlinepatterns import InlineProcessor

from tourist.models import tstore
import xml.etree.ElementTree as etree


class WikiLinkExtension(Extension):
    def __init__(self, **kwargs):
        super(WikiLinkExtension, self).__init__(**kwargs)

    def extendMarkdown(self, md):
        self.md = md

        # append to end of inline patterns
        WIKILINK_RE = r'\[\[([\w0-9_-]+)\]\]' # Non-standard, no support for spaces in link
        wikilinkPattern = WikiLinksInlineProcessor(WIKILINK_RE, self.getConfigs())
        wikilinkPattern.md = md
        md.inlinePatterns.register(wikilinkPattern, 'wikilink', 75)


class WikiLinksInlineProcessor(InlineProcessor):
    def __init__(self, pattern, config):
        super(WikiLinksInlineProcessor, self).__init__(pattern)
        self.config = config

    def handleMatch(self, m, data):
        label = m.group(1).strip()
        if label:
            pool = tstore.Pool.query.filter_by(short_name=label).one_or_none()
            if pool:
                a = etree.Element('a')
                a.text = pool.name
                # All links are internal to the HTML page generated for a single place, at least for
                # now. scripts/batchtool.py looks for links that might go between pages.
                a.set('href', f'#{label}')
            else:
                a = f'[[{label}]]'
        else:
            a = ''
        return a, m.start(0), m.end(0)


def makeExtension(**kwargs):  # pragma: no cover
    return WikiLinkExtension(**kwargs)
