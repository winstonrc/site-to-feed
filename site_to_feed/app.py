import ast
import logging
from bs4.element import DEFAULT_OUTPUT_ENCODING
import nh3
import re
import requests
import toml
import uuid

from bs4 import BeautifulSoup
from collections import namedtuple
from feedgen.feed import FeedGenerator
from flask import Flask, abort, render_template, request, send_from_directory
from flask_htmx import HTMX


app = Flask(__name__)
htmx = HTMX(app)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

FeedEntry = namedtuple('FeedEntry', ['title', 'link', 'content'])


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/documentation')
def documentation():
    return render_template('documentation.html')


@app.route('/feeds/<path:feed_id>.xml')
def feeds(feed_id):
    return send_from_directory('static/feeds', f"{feed_id}.xml")


@app.route('/feeds/<path:feed_id>')
def edit_feed(feed_id):
    return render_template('feed.html', feed_id=feed_id)


# todo: remove?
@app.route('/feed')
def feed():
    feed_id = request.args.get('id')
    return render_template('feed.html', feed_id=feed_id)


@app.route('/get_html_source', methods=['GET'])
def step_1():
    url = request.args.get('url')
    if not url:
        return f'<p>Error: URL is required.</p>'
    logger.debug(f"/get_html: {url=}")

    try:
        response = requests.get(url)
        response.raise_for_status()

        html_source = response.content.decode('utf-8')

        cleaned_html = nh3.clean(html=html_source)

        if htmx:
            return render_template('step_2_define_extraction_rules_htmx.html', html_source=cleaned_html)
        else:
            return render_template('step_2_define_extraction_rules.html', html_source=cleaned_html, url=url)
    except requests.exceptions.ConnectionError as error:
        logger.error(f"{error=}")
        return '<p>Error: Invalid URL.</p>'
    except requests.exceptions.RequestException as error:
        logger.error(f"{error=}")
        return '<p>Error: {error}</p>'


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

    extracted_html = parse_html_via_patterns(
        html_source,
        global_search_pattern,
        item_search_pattern
    )

    if htmx:
        return render_template('step_3_define_output_format_htmx.html', extracted_html=extracted_html, global_search_pattern=global_search_pattern, item_search_pattern=item_search_pattern, url=url)
    else:
        return render_template('step_3_define_output_format.html', extracted_html=extracted_html, global_search_pattern=global_search_pattern, item_search_pattern=item_search_pattern, html_source=html_source, url=url)


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

    item_title_template = request.form.get('item-title-template')
    if not item_title_template:
        return '<p>Error: A string is required for Item Title Template.</p>'
    item_title_position = convert_item_position_str_to_int(item_title_template)

    item_link_template = request.form.get('item-link-template')
    if not item_link_template:
        return '<p>Error: A string is required for Item Link Template.</p>'
    item_link_position = convert_item_position_str_to_int(item_link_template)

    item_content_template = request.form.get('item-content-template')
    if not item_content_template:
        return '<p>Error: A string is required for Item Content Template.</p>'
    item_content_position = convert_item_position_str_to_int(
        item_content_template)

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
        return f'<p>Error: URL from step 1 is required.</p>'

    # Convert the html into a list of named tuples
    feed_entries = create_feed_entries_from_html(
        extracted_html,
        item_title_position,
        item_link_position,
        item_content_position
    )

    # Create a unique id, which is required by ATOM
    feed_id = str(uuid.uuid4()).replace('-', '')
    feeds_filepath = "static/feeds"
    feed_xml_filepath = f"{feeds_filepath}/{feed_id}.xml"
    feed_toml_filepath = f"{feeds_filepath}/{feed_id}.toml"

    # Save values to a toml file to regenerate feed in the future
    toml_data = {
        'url': url,
        'global_search_pattern': global_search_pattern,
        'item_search_pattern': item_search_pattern,
        'feed_title': feed_title,
        'feed_link': feed_link,
        'feed_description': feed_description,
        'item_title_position': item_title_position,
        'item_link_position': item_link_position,
        'item_content_position': item_content_position
    }
    with open(feed_toml_filepath, 'w') as file:
        toml.dump(toml_data, file)

    # Create the feed
    feed = generate_feed(
        feed_id,
        feed_title,
        feed_link,
        feed_description
    )

    add_entries_to_feed(feed, feed_entries)

    if feed_type == 'atom':
        # Get the ATOM feed as string
        # atomfeed = feed.atom_str(pretty=True)
        # Write the ATOM feed to a file
        feed.atom_file(feed_xml_filepath)
    elif feed_type == 'rss':
        # Get the RSS feed as string
        # rssfeed = feed.rss_str(pretty=True)
        # Write the RSS feed to a file
        feed.rss_file(feed_xml_filepath)
    else:
        return '<p>Error: Feed type is required.</p>'

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
            return f'<p>Error: HTML from step 1 is required.</p>'

        return render_template('step_4_get_rss_feed.html', feed=feed_preview, feed_id=feed_id, extracted_html=extracted_html, html_source=html_source, url=url)


def parse_html_via_patterns(html_source: str, global_search_pattern: str, item_search_pattern: str):
    translation_table = str.maketrans("", "", '{}*%"=<>/')

    elements = BeautifulSoup(html_source, 'html.parser')

    if global_search_pattern != "{%}":
        global_search_pattern = global_search_pattern.translate(
            translation_table)
        elements = elements.find(global_search_pattern)

    search_parameters = [str(line)
                         for line in item_search_pattern.splitlines()]
    logger.debug(f"{search_parameters=}")

    # Pop and format the first line, which is used for the initial filtering
    initial_parameter = search_parameters.pop(0).translate(translation_table)

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
                        value = element.a.get_text(strip=True)
                    case 'href':
                        if element.find('a'):
                            value = element.find('a').get(param)
                        else:
                            value = element.get(param)
                    case 'title':
                        if element.title:
                            value = element.title.string
                        elif element.find('a'):
                            value = element.find('a').get(param)
                        else:
                            value = element.get(param)
                    case 'p':
                        value = element.p.get_text()
                    case _:
                        value = element.get(param)
                logger.debug(f"{param=}; {value=}")
                transformed_element.append(value)
            except Exception as error:
                logger.error(f"{error=}")
                return '<p>Error: Error parsing elements. Please go back and check your query again.</p>'
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
            title=values[item_title_position],
            link=values[item_link_position],
            content=values[item_content_position]
        )
        feed_entries.append(entry)
    return feed_entries


def convert_item_position_str_to_int(number_str: str) -> int:
    match = re.search(r'{%(\d+)}', number_str)
    if match:
        return int(match.group(1)) - 1
    else:
        return '<p>Error: A string of an int is required.</p>'


if __name__ == '__main__':
    app.run(debug=True)
