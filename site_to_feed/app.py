import ast
import logging
import nh3
import re
import requests
import uuid

from bs4 import BeautifulSoup
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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/documentation')
def documentation():
    return render_template('documentation.html')


@app.route('/feeds/<path:filename>')
def feeds(filename):
    return send_from_directory('static/feeds', filename)


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
        abort(500, 'Error: Invalid URL.')
    except requests.exceptions.RequestException as error:
        logger.error(f"{error=}")
        abort(500, 'Error: {error}')


@app.route('/extract_html', methods=['POST'])
def step_2():
    global_search_pattern = request.form.get('global-search-pattern')
    if not global_search_pattern:
        abort(500, 'Error: A string is required for Global Search Pattern.')

    item_search_pattern = request.form.get('item-search-pattern')
    if not item_search_pattern:
        abort(500, 'Error: A string is required for Item Search Pattern.')

    html_source = request.form.get('html-source')
    if not html_source:
        abort(500, 'Error: HTML source from step 1 is required.')

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
                abort(
                    500, 'Error: Error parsing elements. Please go back and check your query again.')
            extracted_html[i] = transformed_element

    logger.debug(
        f"{global_search_pattern=}\n{item_search_pattern=}\n{extracted_html=}"
    )

    if htmx:
        return render_template('step_3_define_output_format_htmx.html', extracted_html=extracted_html)
    else:
        url = request.form.get('url')
        if not url:
            abort(500, 'Error: URL from step 1 is required.')

        return render_template('step_3_define_output_format.html', extracted_html=extracted_html, html_source=html_source, url=url)


@app.route('/format_feed_output', methods=['POST'])
def step_3():
    feed_title = request.form.get('feed-title')
    if not feed_title:
        abort(500, 'Error: A string is required for Feed Title.')

    feed_link = request.form.get('feed-link')
    if not feed_link:
        abort(500, 'Error: A string is required for Feed Link.')

    feed_description = request.form.get('feed-description')
    if not feed_description:
        abort(500, 'Error: A string is required for Feed Description.')

    item_title_template = request.form.get('item-title-template')
    if not item_title_template:
        abort(500, 'Error: A string is required for Item Title Template.')
    item_title_position = extract_number(item_title_template)

    item_link_template = request.form.get('item-link-template')
    if not item_link_template:
        abort(500, 'Error: A string is required for Item Link Template.')
    item_link_position = extract_number(item_link_template)

    item_content_template = request.form.get('item-content-template')
    if not item_content_template:
        abort(500, 'Error: A string is required for Item Content Template.')
    item_content_position = extract_number(item_content_template)

    feed_type = request.form.get('feed-type')
    if not feed_type:
        abort(500, 'Error: A feed type is required.')

    extracted_html = request.form.get('extracted-html')
    if not extracted_html:
        abort(500, 'Error: Extracted HTML from step 2 is required.')

    # Convert extracted_html from a str back into a dict
    extracted_html = ast.literal_eval(extracted_html)

    # Create a unique id, which is required by ATOM
    feed_id = str(uuid.uuid4())

    # Create filename
    filename = f"{feed_id}.xml"

    # Set path where feed will be saved to
    feed_filepath = f"static/feeds/{filename}"

    # Create the feed
    fg = FeedGenerator()
    fg.id(feed_id)
    fg.title(feed_title)
    fg.link(href=feed_link, rel='self')
    fg.subtitle(feed_description)
    fg.language('en')

    feed_entries = []
    for key, values in extracted_html.items():
        # Add entry to feedgen
        fe = fg.add_entry()
        fe.id(f"{feed_id}/{key}")
        fe.title(values[item_title_position])
        fe.link(href=values[item_link_position])
        fe.content(values[item_content_position])

        # Add entry to array that will be passed to the template
        feed_entries.append({
            'title': values[item_title_position],
            'link': values[item_link_position],
            'content': values[item_content_position]
        })

    # Create a dict to pass to the template to preview the feed
    feed = {
        'title': feed_title,
        'link': feed_link,
        'subtitle': feed_description,
        'entries': feed_entries
    }

    if feed_type == 'atom':
        # Get the ATOM feed as string
        atomfeed = fg.atom_str(pretty=True)
        # Write the ATOM feed to a file
        fg.atom_file(feed_filepath)
    elif feed_type == 'rss':
        # Get the RSS feed as string
        rssfeed = fg.rss_str(pretty=True)
        # Write the RSS feed to a file
        fg.rss_file(feed_filepath)
    else:
        abort(500, 'Error: Feed type is required.')

    if htmx:
        return render_template('step_4_get_rss_feed_htmx.html', feed=feed, filename=filename, feed_id=feed_id)
    else:
        url = request.form.get('url')
        if not url:
            return f'<p>Error: URL from step 1 is required.</p>'

        html_source = request.form.get('html-source')
        if not html_source:
            return f'<p>Error: HTML from step 1 is required.</p>'

        return render_template('step_4_get_rss_feed.html', feed=feed, filename=filename, feed_id=feed_id, extracted_html=extracted_html, html_source=html_source, url=url)


def extract_number(number_str):
    match = re.search(r'{%(\d+)}', number_str)
    if match:
        return int(match.group(1)) - 1
    else:
        abort(500, 'Error: A string of an int is required.')


if __name__ == '__main__':
    app.run(debug=True)
