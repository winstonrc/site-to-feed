# site-to-feed

This project allows a user to enter a website URL, apply a pattern to website's HTML source, and create a custom RSS feed to subscribe to.

## Usage

Complete usage documentation can be located at the /documentation route.
Brief instructions are provided in a collapsed sections titled "Instructions" on the main page.

## Dependencies

This project consists of the following dependencies:

- [Flask](https://flask.palletsprojects.com): Backend framework
- [htmx](https://htmx.org/): Progressively enhances the page if JavaScript is enabled
- [flask_htmx](https://github.com/edmondchuc/flask-htmx): Handle route delivery depending on whether or not JavaScript is enabled
- [nh3](https://github.com/messense/nh3): Sanitizes the retrieved HTML
- [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/): Parses the HTML
- [feedgen](https://github.com/lkiesow/python-feedgen): Generates RSS and ATOM feeds
