import ast
import logging
import json
import nh3
import os
import re
import requests
import requests_cache
import toml
import uuid

from bs4 import BeautifulSoup
from bs4.element import DEFAULT_OUTPUT_ENCODING
from collections import namedtuple
from datetime import timedelta
from dotenv import load_dotenv
from feedgen.feed import FeedGenerator
from flask import Flask, abort, make_response, redirect, render_template, request, send_from_directory, url_for
from flask_htmx import HTMX
from urllib.parse import urljoin, urlsplit


dotenv_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=dotenv_path)

DATA_DIRECTORY = '/data/feeds/'
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_API_URL = os.getenv("OPENAI_API_URL")
LLM_BASE_QUERY = os.getenv("LLM_BASE_QUERY")
DATA_DIRECTORY = os.getenv("DATA_DIRECTORY")
FEEDS_DIRECTORY = f'{DATA_DIRECTORY}/feeds'
os.makedirs(FEEDS_DIRECTORY, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

FeedEntry = namedtuple('FeedEntry', ['title', 'link', 'content'])

app = Flask(__name__)
htmx = HTMX(app)

session = requests_cache.CachedSession(
    'http_cache',
    backend='sqlite',
    use_temp=True
)


class FeedConfig:
    def __init__(self, filepath):
        self.filepath = filepath
        with open(filepath, 'r') as file:
            self._data = toml.load(file)

    @property
    def url(self) -> str:
        return self._data['url']

    @url.setter
    def url(self, value: str):
        self._data['url'] = value

    @property
    def global_search_pattern(self) -> str:
        return self._data['global_search_pattern']

    @global_search_pattern.setter
    def global_search_pattern(self, value: str):
        self._data['global_search_pattern'] = value

    @property
    def item_search_pattern(self) -> str:
        return self._data['item_search_pattern']

    @item_search_pattern.setter
    def item_search_pattern(self, value: str):
        self._data['item_search_pattern'] = value

    @property
    def feed_title(self) -> str:
        return self._data['feed_title']

    @feed_title.setter
    def feed_title(self, value: str):
        self._data['feed_title'] = value

    @property
    def feed_link(self) -> str:
        return self._data['feed_link']

    @feed_link.setter
    def feed_link(self, value: str):
        self._data['feed_link'] = value

    @property
    def feed_description(self) -> str:
        return self._data['feed_description']

    @feed_description.setter
    def feed_description(self, value: str):
        self._data['feed_description'] = value

    @property
    def item_title_position(self) -> int:
        return int(self._data['item_title_position'])

    @item_title_position.setter
    def item_title_position(self, value: int):
        self._data['item_title_position'] = value

    @property
    def item_link_position(self) -> int:
        return int(self._data['item_link_position'])

    @item_link_position.setter
    def item_link_position(self, value: int):
        self._data['item_link_position'] = value

    @property
    def item_content_position(self) -> int:
        return int(self._data['item_content_position'])

    @item_content_position.setter
    def item_content_position(self, value: int):
        self._data['item_content_position'] = value

    @property
    def feed_type(self) -> str:
        return self._data['feed_type']

    @feed_type.setter
    def feed_type(self, value: str):
        self._data['feed_type'] = value

    def save(self):
        with open(self.filepath, 'w') as file:
            toml.dump(self._data, file)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/documentation')
def documentation():
    return render_template('documentation.html')


@app.route('/feeds/<path:feed_id>.xml', methods=['GET'])
def feed_file(feed_id):
    return send_from_directory(FEEDS_DIRECTORY, f"{feed_id}.xml")


@app.route('/feeds/<path:feed_id>', methods=['GET'])
def view_feed(feed_id):
    feed_xml_filepath = f"{FEEDS_DIRECTORY}/{feed_id}.xml"
    feed_toml_filepath = f"{FEEDS_DIRECTORY}/{feed_id}.toml"

    if not os.path.exists(feed_xml_filepath) or not os.path.exists(feed_toml_filepath):
        # If the files don't exist, issue a 404 error
        abort(404)

    config = FeedConfig(feed_toml_filepath)

    html_source = get_html(config.url)

    extracted_html = parse_html_via_patterns(
        html_source,
        config.global_search_pattern,
        config.item_search_pattern,
        config.feed_link
    )

    # Convert the html into a list of named tuples
    feed_entries = create_feed_entries_from_html(
        extracted_html,
        config.item_title_position,
        config.item_link_position,
        config.item_content_position
    )

    # Create a dict to pass to the template to preview the feed
    feed_preview = {
        'title': config.feed_title,
        'link': config.feed_link,
        'subtitle': config.feed_description,
        'entries': feed_entries
    }

    return render_template(
        'feed.html',
        feed=feed_preview,
        feed_id=feed_id,
        item_title_position=config.item_title_position,
        item_link_position=config.item_link_position,
        item_content_position=config.item_content_position
    )


@app.route('/feeds/<path:feed_id>', methods=['POST'])
def edit_feed(feed_id):
    feed_xml_filepath = f"{FEEDS_DIRECTORY}/{feed_id}.xml"
    feed_toml_filepath = f"{FEEDS_DIRECTORY}/{feed_id}.toml"

    if not os.path.exists(feed_xml_filepath) or not os.path.exists(feed_toml_filepath):
        # If the files don't exist, issue a 404 error
        abort(404)

    config = FeedConfig(feed_toml_filepath)

    feed_title = request.form.get('feed-title')
    if feed_title:
        config.feed_title = feed_title
        config.save()

    feed_link = request.form.get('feed-link')
    if feed_link:
        config.feed_link = feed_link
        config.save()

    feed_description = request.form.get('feed-description')
    if feed_description:
        config.feed_description = feed_description
        config.save()

    html_source = get_html(config.url)

    try:
        extracted_html = parse_html_via_patterns(
            html_source,
            config.global_search_pattern,
            config.item_search_pattern,
            config.feed_link
        )
    except Exception as error:
        logger.error(f"{error=}")
        return '<p>Error extracting HTML. Please try changing your item search pattern.</p>'

    # Create the feed
    feed = generate_feed(
        feed_id,
        config.feed_title,
        config.feed_link,
        config.feed_description
    )

    # Convert the html into a list of named tuples
    feed_entries = create_feed_entries_from_html(
        extracted_html,
        config.item_title_position,
        config.item_link_position,
        config.item_content_position
    )

    add_entries_to_feed(feed, feed_entries)

    try:
        if config.feed_type == 'atom':
            # Write the ATOM feed to a file
            feed.atom_file(feed_xml_filepath)
        elif config.feed_type == 'rss':
            # Write the RSS feed to a file
            feed.rss_file(feed_xml_filepath)
        else:
            return '<p>Error: Feed type is required.</p>'
    except ValueError as error:
        logger.error(f"{error=}")
        return '<p>Error: Feed title is required.</p>'
    except Exception as error:
        logger.error(f"{error=}")
        return '<p>Error: Unable to create feed.</p>'

    # Create a dict to pass to the template to preview the feed
    feed_preview = {
        'title': feed_title,
        'link': feed_link,
        'subtitle': feed_description,
        'entries': feed_entries
    }

    return render_template('feed.html', feed_id=feed_id, feed=feed_preview)


@app.route('/feeds/<path:feed_id>/delete', methods=['POST', 'DELETE'])
def delete_feed(feed_id):
    feed_xml_filepath = f"{FEEDS_DIRECTORY}/{feed_id}.xml"
    feed_toml_filepath = f"{FEEDS_DIRECTORY}/{feed_id}.toml"

    if os.path.exists(feed_xml_filepath) or os.path.exists(feed_toml_filepath):
        if os.path.exists(feed_xml_filepath):
            os.remove(feed_xml_filepath)
        else:
            logger.error('Feed XML file does not exist.')

        if os.path.exists(feed_toml_filepath):
            os.remove(feed_toml_filepath)
        else:
            logger.error('Feed TOML file does not exist.')
    else:
        return '<p>Error: Feed file does not exist.</p>'

    if htmx:
        response = make_response(render_template('index.html'), 200)
        response.headers['HX-Redirect'] = url_for('index')
        return response
    else:
        return redirect(url_for('index'))


@app.route('/get_html_source', methods=['GET'])
def step_1():
    url = request.args.get('url')
    if not url:
        return '<p>Error: URL is required.</p>'
    logger.debug(f"/get_html: {url=}")

    html_source = get_html(url)

    # Manual process
    if 'get_html' in request.args:
        if htmx:
            return render_template('step_2_define_extraction_rules_htmx.html', html_source=html_source)
        else:
            return render_template('step_2_define_extraction_rules.html', html_source=html_source, url=url)
    # LLM process
    else:
        if LLM_BASE_QUERY is None or LLM_API_URL is None or LLM_API_KEY is None:
            return '<p>Error: Missing required environment variable for LLM query.</p>'

        # Only send the first 50 lines of the HTML to the API since
        # there are size limits, and the pattern should be contained.
        partial_html_source_content = '\n'.join(html_source.splitlines()[:250])
        logger.debug(partial_html_source_content)

        # Post data to the LLM API
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": LLM_BASE_QUERY
                },
                {
                    "role": "user",
                    "content": partial_html_source_content
                }
            ]
        }

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {LLM_API_KEY}'
        }

        try:
            response = session.post(LLM_API_URL, headers=headers, json=data)
            response.raise_for_status()
            logger.info(f"Request successful: {response.status_code}")
        except requests.exceptions.HTTPError as error:
            logger.error(f"{error=}.\n{error.response.text}")
            return f"<p>{error}<br />I'm Feeling Lucky is temporarily out of service. Please try using the manual 'Get HTML' process.</p>"
        except requests.exceptions.ConnectionError as error:
            logger.error(f"{error=}")
            return "<p>Error: Invalid URL.</p>"
        except requests.exceptions.RequestException as error:
            logger.error(f"{error=}")
            return f"<p>{error}<br />I'm Feeling Lucky is temporarily out of service. Please try using the manual 'Get HTML' process.</p>"

        response_data = response.json()
        logger.debug(f"{response_data=}")

        response_content_json = response_data.get(
            'choices')[0].get('message').get('content')

        if not response_content_json:
            logger.error(f"Error unpacking response_data: {response_data=}")
            return f"<p>Error: I'm Feeling Lucky is temporarily out of service. Please try using the manual 'Get HTML' process.</p>"

        response_content = json.loads(response_content_json)
        logger.info(f"{response_content=}")
        page_title = response_content.get('page_title')
        global_search_pattern = response_content.get(
            'global_search_pattern', '*')
        opening_element = response_content.get('opening_element')
        item_title = response_content.get('item_title')
        item_link = response_content.get('item_link')
        item_content = response_content.get('item_content')

        if not page_title or not opening_element or not item_title or not item_link or not item_content:
            logger.error(
                f"Error unpacking response_json: {response_content_json=}"
            )
            return "<p>Error: I'm Feeling Lucky is temporarily out of service. Please try using the manual 'Get HTML' process.</p>"

        item_search_pattern = f"{opening_element}\n{item_title}\n{item_link}\n{item_content}"
        logger.debug(f"{item_search_pattern=}")

        feed_id = str(uuid.uuid4()).replace('-', '')

        retrieved_title = get_page_title(html_source)

        if retrieved_title and retrieved_title != page_title:
            feed_title = retrieved_title
        else:
            feed_title = page_title

        feed_link = url
        feed_description = "Custom feed generated by https://github.com/winstonrc/site-to-feed/"

        # Create the feed
        feed = generate_feed(
            feed_id,
            feed_title,
            feed_link,
            feed_description
        )

        try:
            extracted_html = parse_html_via_patterns(
                html_source,
                global_search_pattern,
                item_search_pattern,
                url
            )
        except Exception as error:
            logger.error(f"{error=}")
            return "<p>Error: I'm Feeling Lucky is temporarily out of service. Please try using the manual 'Get HTML' process.</p>"

        item_title_position = 1
        item_link_position = 2
        item_content_position = 3

        # Convert the html into a list of named tuples
        feed_entries = create_feed_entries_from_html(
            extracted_html,
            item_title_position,
            item_link_position,
            item_content_position
        )

        add_entries_to_feed(feed, feed_entries)

        try:
            # Write the ATOM feed to a file
            feed_filepath = f"{FEEDS_DIRECTORY}/{feed_id}"
            feed.atom_file(f"{feed_filepath}.xml")
        except ValueError as error:
            logger.error(
                f"{error=}; This is most likely due to a missing title for a feed entry resulting from difficulty parsing the HTML correctly.")
            return "<p>Error: I'm Feeling Lucky is temporarily out of service. Please try using the manual 'Get HTML' process.</p>"
        except Exception as error:
            logger.error(f"{error=}")
            return "<p>Error: I'm Feeling Lucky is temporarily out of service. Please try using the manual 'Get HTML' process.</p>"

        # Save values to a toml file to regenerate feed in the future
        config = {
            'url': url,
            'global_search_pattern': global_search_pattern,
            'item_search_pattern': item_search_pattern,
            'feed_title': feed_title,
            'feed_link': feed_link,
            'feed_description': feed_description,
            'item_title_position': item_title_position,
            'item_link_position': item_link_position,
            'item_content_position': item_content_position,
            'feed_type': "atom"
        }
        with open(f"{feed_filepath}.toml", 'w') as file:
            toml.dump(config, file)

        # Create a dict to pass to the template to preview the feed
        feed_preview = {
            'title': feed_title,
            'link': feed_link,
            'subtitle': feed_description,
            'entries': feed_entries
        }

        if htmx:
            return render_template('im_feeling_lucky_htmx.html', feed=feed_preview, feed_id=feed_id)
        else:
            return render_template('im_feeling_lucky.html', feed=feed_preview, feed_id=feed_id)


@app.route('/extract_html', methods=['POST'])
def step_2():
    global_search_pattern = request.form.get('global-search-pattern')
    if not global_search_pattern:
        return '<p>Error: A string is required for Global Search Pattern.</p>'

    item_search_pattern = request.form.get('item-search-pattern')
    if not item_search_pattern:
        return '<p>Error: A string is required for Item Search Pattern.</p>'

    html_source = request.form.get('html-source')
    if not html_source:
        return '<p>Error: HTML source from step 1 is required.</p>'

    url = request.form.get('url')
    if not url:
        return '<p>Error: URL from step 1 is required.</p>'

    try:
        extracted_html = parse_html_via_patterns(
            html_source,
            global_search_pattern,
            item_search_pattern,
            url
        )
    except Exception as error:
        logger.error(f"{error=}")
        return '<p>Error extracting HTML. Please try changing your item search pattern.</p>'

    title = get_page_title(html_source)

    # Create a unique id, which is required by ATOM.
    # Placing this here as an input for the step 3 form so only 1 feed
    # is generated if the user hits "Generate feed" to submit the form
    # multiple times.
    feed_id = str(uuid.uuid4()).replace('-', '')

    if htmx:
        return render_template('step_3_define_output_format_htmx.html', extracted_html=extracted_html, global_search_pattern=global_search_pattern, item_search_pattern=item_search_pattern, feed_id=feed_id, title=title, url=url)
    else:
        return render_template('step_3_define_output_format.html', extracted_html=extracted_html, global_search_pattern=global_search_pattern, item_search_pattern=item_search_pattern, feed_id=feed_id, title=title, html_source=html_source, url=url)


@app.route('/format_feed_output', methods=['POST'])
def step_3():
    feed_title = request.form.get('feed-title')
    if not feed_title:
        return '<p>Error: A string is required for Feed Title.</p>'

    feed_link = request.form.get('feed-link')
    if not feed_link:
        return '<p>Error: A string is required for Feed Link.</p>'

    feed_description = request.form.get('feed-description')
    if not feed_description:
        return '<p>Error: A string is required for Feed Description.</p>'

    item_title_position = request.form.get('item-title-position')
    if not item_title_position:
        return '<p>Error: A number is required for Item Title Position.</p>'
    item_title_position = int(item_title_position)

    item_link_position = request.form.get('item-link-position')
    if not item_link_position:
        return '<p>Error: A number is required for Item Link Position.</p>'
    item_link_position = int(item_link_position)

    item_content_position = request.form.get('item-content-position')
    if not item_content_position:
        return '<p>Error: A number is required for Item Content Position.</p>'
    item_content_position = int(item_content_position)

    feed_type = request.form.get('feed-type')
    if not feed_type:
        return '<p>Error: A feed type is required.</p>'

    extracted_html = request.form.get('extracted-html')
    if not extracted_html:
        return '<p>Error: Extracted HTML from step 2 is required.</p>'
    # Convert extracted_html from a str back into a dict
    extracted_html = ast.literal_eval(extracted_html)

    global_search_pattern = request.form.get('global-search-pattern')
    if not global_search_pattern:
        return '<p>Error: A string is required for Global Search Pattern.</p>'

    item_search_pattern = request.form.get('item-search-pattern')
    if not item_search_pattern:
        return '<p>Error: A string is required for Item Search Pattern.</p>'

    url = request.form.get('url')
    if not url:
        return '<p>Error: URL from step 1 is required.</p>'

    feed_id = request.form.get('feed-id')
    if not feed_id:
        return '<p>Error: feed_id from step 2 is required.</p>'

    # Create the feed
    feed = generate_feed(
        feed_id,
        feed_title,
        feed_link,
        feed_description
    )

    # Convert the html into a list of named tuples
    feed_entries = create_feed_entries_from_html(
        extracted_html,
        item_title_position,
        item_link_position,
        item_content_position
    )

    add_entries_to_feed(feed, feed_entries)

    feed_filepath = f"{FEEDS_DIRECTORY}/{feed_id}"
    try:
        if feed_type == 'atom':
            # Write the ATOM feed to a file
            feed.atom_file(f"{feed_filepath}.xml")
        elif feed_type == 'rss':
            # Write the RSS feed to a file
            feed.rss_file(f"{feed_filepath}.xml")
        else:
            return '<p>Error: Feed type is required.</p>'
    except ValueError as error:
        logger.error(f"{error=}")
        return '<p>Error: Feed title is required.</p>'
    except Exception as error:
        logger.error(f"{error=}")
        return '<p>Error: Unable to create feed.</p>'

    # Save values to a toml file to regenerate feed in the future
    config = {
        'url': url,
        'global_search_pattern': global_search_pattern,
        'item_search_pattern': item_search_pattern,
        'feed_title': feed_title,
        'feed_link': feed_link,
        'feed_description': feed_description,
        'item_title_position': item_title_position,
        'item_link_position': item_link_position,
        'item_content_position': item_content_position,
        'feed_type': feed_type
    }
    with open(f"{feed_filepath}.toml", 'w') as file:
        toml.dump(config, file)

    # Create a dict to pass to the template to preview the feed
    feed_preview = {
        'title': feed_title,
        'link': feed_link,
        'subtitle': feed_description,
        'entries': feed_entries
    }

    if htmx:
        return render_template('step_4_get_rss_feed_htmx.html', feed=feed_preview, feed_id=feed_id)
    else:
        html_source = request.form.get('html-source')
        if not html_source:
            return '<p>Error: HTML from step 1 is required.</p>'

        return render_template('step_4_get_rss_feed.html', feed=feed_preview, feed_id=feed_id, extracted_html=extracted_html, html_source=html_source, url=url)


def get_html(url: str):
    try:
        response = session.get(url)
        response.raise_for_status()
        logger.info(f"Request successful: {response.status_code}")

        html_source = response.content.decode('utf-8')
        sanitized_html = nh3.clean(html=html_source)
        soup = BeautifulSoup(sanitized_html, 'html.parser')
        pretty_html = soup.prettify()

        return pretty_html
    except requests.exceptions.HTTPError as error:
        logger.error(f"{error=}")
        return f'<p>Error: HTTP error occurred. {error}</p>'
    except requests.exceptions.ConnectionError as error:
        logger.error(f"{error=}")
        return '<p>Error: Invalid URL.</p>'
    except requests.exceptions.RequestException as error:
        logger.error(f"{error=}")
        return f"<p>Error: {error}</p>"


def is_absolute_url(url):
    # Split the URL into components
    url_components = urlsplit(url)

    # Check if the scheme and netloc components are present (indicating an absolute URL)
    return bool(url_components.scheme and url_components.netloc)


def get_page_title(html_doc: str) -> str:
    soup = BeautifulSoup(html_doc, 'html.parser')

    title = soup.title
    if title and title.string:
        return title.string.strip()

    header = soup.header
    if header:
        header_title = header.find(['h1', 'h2'])
        if header_title and header_title.text:
            return header_title.text.strip()

    return ''


def parse_html_via_patterns(html_doc: str, global_search_pattern: str, item_search_pattern: str, base_url: str) -> dict[int, list]:
    translation_table = str.maketrans("", "", '{}*%"=<>/')

    elements = BeautifulSoup(html_doc, 'html.parser')

    if global_search_pattern != "*":
        global_search_pattern = global_search_pattern.translate(
            translation_table)
        elements = elements.find(global_search_pattern)

    search_parameters = [str(line)
                         for line in item_search_pattern.splitlines()]
    logger.debug(f"{search_parameters=}")

    # Pop and format the first line, which is used for the initial filtering
    initial_parameter = search_parameters.pop(0).translate(translation_table)

    # pyright issues a warning about find_all being an unknown member
    # of a NavigableString.
    # It seems to be working correctly, so I'm ignoring the warning.
    elements = elements.find_all(initial_parameter)
    logger.debug(f"{len(elements)=}\n{elements=}")

    extracted_html = {}
    for i, element in enumerate(elements, start=1):
        logger.debug(f"{element=}")

        transformed_element = []
        for param in search_parameters:
            # Remove matching closing tag
            param = re.sub(r'</[a-zA-Z]+>', '', param)
            # Retrieve element or attribute name
            param = param.translate(translation_table)

            try:
                match param:
                    case 'a':
                        if element.a:
                            value = element.a.get_text(strip=True)
                        else:
                            value = element.get(param)
                    case 'href':
                        if element.a:
                            href = element.find('a').get(param)
                        else:
                            href = element.get(param)

                        if is_absolute_url(href):
                            value = href
                        else:
                            value = urljoin(base_url, href)
                    case 'p':
                        if element.p:
                            value = element.p.get_text()
                        else:
                            value = element.get(param)
                    case _:
                        value = element.get(param)
                logger.debug(f"{param=}; {value=}")
                transformed_element.append(value)
            except Exception as error:
                logger.error(
                    f"Error: Error parsing elements;\n{error=};\n{param=}")
                abort(
                    500, '<p>Error: Error parsing elements. Please go back and check your query again.</p>')
            extracted_html[i] = transformed_element

    return extracted_html


def generate_feed(feed_id: str, feed_title: str, feed_link: str, feed_description, feed_language: str = 'en') -> FeedGenerator:
    fg = FeedGenerator()
    fg.id(feed_id)
    fg.title(feed_title)
    fg.link(href=feed_link, rel='self')
    fg.subtitle(feed_description)
    fg.language(feed_language)

    return fg


def add_entries_to_feed(fg: FeedGenerator, entries: list[FeedEntry]) -> None:
    """
    The entries do not currently posses a datetime.
    They appear in the correct order in the feed preview, but they are
    reversed in the feed itself because FeedGenerator adds entries
    oldest to newest by default without a pubDate().
    Accordingly, reversing the entries list before iteration will add
    the oldest content first, which results in the newest entries
    displaying first in the generated xml file.

    todo: Look into updating this in case we receive entries that have
    a datetime to set for fe.pubDate().
    """
    reversed_entries = list(reversed(entries))

    feed_id = fg.id()

    for i, entry in enumerate(reversed_entries, start=1):
        fe = fg.add_entry()
        fe.id(f"{feed_id}/{i}")
        fe.title(entry.title)
        fe.link(href=entry.link)
        fe.content(entry.content)


def create_feed_entries_from_html(html: dict, item_title_position: int, item_link_position: int, item_content_position: int) -> list[FeedEntry]:
    feed_entries = []
    for key, values in html.items():
        entry = FeedEntry(
            title=values[item_title_position - 1],
            link=values[item_link_position - 1],
            content=values[item_content_position - 1]
        )
        feed_entries.append(entry)
    return feed_entries


if __name__ == '__main__':
    app.run(debug=True)
