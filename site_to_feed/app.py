import logging
import nh3
import requests

from bs4 import BeautifulSoup
from flask import Flask, render_template, request


app = Flask(__name__)

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


@app.route('/get_html', methods=['GET'])
def step_1():
    url = request.args.get('url')
    logger.debug(f"/get_html: {url=}")

    if not url:
        return f'<p>Error: URL required</p>'

    try:
        response = requests.get(url)
        response.raise_for_status()

        html_source = response.content.decode('utf-8')

        cleaned_html = nh3.clean(html=html_source)

        return render_template('get_html.html', url=url, html_source=cleaned_html)
    except requests.exceptions.ConnectionError as error:
        logger.error(f"{error=}")
        return f'<p>Error: Invalid URL</p>'
    except requests.exceptions.RequestException as error:
        logger.error(f"{error=}")
        return f'<p>Error: {error}</p>'


@app.route('/extract_html', methods=['POST'])
def step_2():
    global_search_pattern = request.form.get('global-search-pattern')
    if not global_search_pattern:
        return f'<p>Error: A string is required for Global Search Pattern.</p>'

    item_search_pattern = request.form.get('item-search-pattern')
    if not item_search_pattern:
        return f'<p>Error: A string is required for Item Search Pattern.</p>'

    html_source = request.form.get('html-source')
    if not html_source:
        return f'<p>Error: HTML source from step 1 is required.</p>'

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
                return f'<p>Error: Error parsing elements. Please go back and check your query again.</p>'
            extracted_html[i] = transformed_element

    logger.debug(
        f"{global_search_pattern=}\n{item_search_pattern=}\n{extracted_html=}"
    )
    return render_template('extract_html.html', extracted_html=extracted_html, html_source=html_source)


@app.route('/format_feed_output', methods=['POST'])
def step_3():
    feed_title = request.form.get('feed-title')
    if not feed_title:
        return f'<p>Error: A string is required for Feed Title.</p>'

    feed_link = request.form.get('feed-link')
    if not feed_link:
        return f'<p>Error: A string is required for Feed Link.</p>'

    feed_description = request.form.get('feed-description')
    if not feed_description:
        return f'<p>Error: A string is required for Feed Description.</p>'

    item_title_template = request.form.get('item-title-template')
    if not item_title_template:
        return f'<p>Error: A string is required for Item Title Template.</p>'

    item_link_template = request.form.get('item-link-template')
    if not item_link_template:
        return f'<p>Error: A string is required for Item Link Template.</p>'

    item_content_template = request.form.get('item-content-template')
    if not item_content_template:
        return f'<p>Error: A string is required for Item Content Template.</p>'

    extracted_html = request.form.get('extracted-html')
    if not extracted_html:
        return f'<p>Error: Extracted HTML from step 2 is required.</p>'

    # todo: implement
    feed_preview = extracted_html

    return render_template('format_feed_output.html', feed_title=feed_title, feed_link=feed_link, feed_description=feed_description, feed_preview=feed_preview)


if __name__ == '__main__':
    app.run(debug=True)
