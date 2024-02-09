import logging
import nh3
import re
import requests

from bs4 import BeautifulSoup
from flask import Flask, render_template, request, session


app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@app.route('/')
def step_1():
    return render_template('index.html')


@app.route('/documentation')
def documentation():
    return render_template('documentation.html')


@app.route('/get_html', methods=['GET'])
def step_2():
    url = request.args.get('url')
    logger.debug(f"/get_html: {url=}")

    if not url:
        return f'<p>Error: URL required</p>'

    try:
        response = requests.get(url)
        response.raise_for_status()

        html_source = response.text

        cleaned_html = nh3.clean(html=html_source)

        return render_template('get_html.html', url=url, html_source=cleaned_html)
    except requests.exceptions.ConnectionError as error:
        logger.error(f"{error=}")
        return f'<p>Error: Invalid URL</p>'
    except requests.exceptions.RequestException as error:
        logger.error(f"{error=}")
        return f'<p>Error: {error}</p>'


@app.route('/extract_html', methods=['POST'])
def step_3():
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

    if global_search_pattern == "{%}":
        elements = BeautifulSoup(html_source, 'html.parser')
    else:
        elements = BeautifulSoup(html_source, 'html.parser')
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

    extracted_elements = []
    for i, element in enumerate(elements, start=1):
        logger.debug(f"{element=}")
        transformed_element = f"Item {i}\n"

        for i, param in enumerate(search_parameters, start=1):
            param = re.sub(r'</[a-zA-Z]+>', '', param)
            param = param.translate(translation_table)

            match param:
                case 'a':
                    value = element.get_text(strip=True)
                case 'href':
                    if element.get(param):
                        value = element.get(param)
                    else:
                        value = element.find('a').get(param)
                case 'title':
                    if element.get(param):
                        value = element.get(param)
                    else:
                        value = element.find('a').get(param)
                case 'p':
                    value = element.get_text(strip=True)
                case _:
                    value = element.get(param)

            logger.debug(f"{param=}; {value=}")

            transformed_element += f"{{%{i}}} = {str(value)}\n"

        extracted_elements.append(transformed_element)

    extracted_html = '\n'.join([str(element)
                                for element in extracted_elements])

    logger.debug(
        f"{global_search_pattern=}\n{item_search_pattern=}\n{extracted_html=}"
    )
    return render_template('extract_html.html', extracted_html=extracted_html, html_source=html_source)


if __name__ == '__main__':
    app.run(debug=True)
