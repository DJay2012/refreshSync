import json
import unittest
from unittest.mock import Mock, patch

from allSearchAPI.app.scraping import Scraper, _extract_ai_article, _extract_html_article


class ArticleExtractionTests(unittest.TestCase):
    def setUp(self):
        navigation = "Advertisement " + " ".join(
            f"[Menu {index}](https://example.com/menu/{index})" for index in range(8)
        )
        self.page = (
            navigation
            + " # Council Debates New Voting Software"
            + " ## Officials said the system will be reviewed next week."
            + " - [News Desk](https://example.com/authors/news-desk)"
            + " - Published On 24 September 2026 Read Time: 3 mins"
            + " ![Hero](https://example.com/hero.jpg)"
            + " **New Delhi:** Officials opened a review of the voting software after"
            + " several members raised concerns about access to records."
            + " The council will publish its findings next week."
            + " ### Related News [Another story](https://example.com/another)"
        )

    def test_flattened_page_extracts_distinct_fields(self):
        title, summary, content = _extract_ai_article(self.page)
        self.assertEqual(title, "Council Debates New Voting Software")
        self.assertEqual(summary, "Officials said the system will be reviewed next week.")
        self.assertEqual(
            content,
            "New Delhi: Officials opened a review of the voting software after "
            "several members raised concerns about access to records. "
            "The council will publish its findings next week.",
        )

    def test_json_wrapped_page_is_recovered(self):
        title, summary, content = _extract_ai_article(
            json.dumps({"headline": "Advertisement", "summary": "Advertisement", "content": self.page})
        )
        self.assertEqual(title, "Council Debates New Voting Software")
        self.assertEqual(summary, "Officials said the system will be reviewed next week.")
        self.assertTrue(content.startswith("New Delhi:"))

    def test_multiline_article_without_dateline(self):
        page = (
            "Advertisement " + " ".join(
                f"[Menu {index}](https://example.com/menu/{index})" for index in range(8)
            )
            + "\n# Council Debates New Voting Software"
            + "\n## Officials said the system will be reviewed next week."
            + "\nThe council opened a review of the voting software after members"
            + " raised concerns about access to records."
            + "\n### Related News\nAnother story"
        )
        title, summary, content = _extract_ai_article(page)
        self.assertEqual(title, "Council Debates New Voting Software")
        self.assertEqual(summary, "Officials said the system will be reviewed next week.")
        self.assertEqual(
            content,
            "The council opened a review of the voting software after members "
            "raised concerns about access to records.",
        )

    def test_flattened_article_with_byline_highlights_and_sections(self):
        page = (
            " ".join(f"[Menu {index}](https://example.com/{index})" for index in range(8))
            + " # New Phone Launched With Larger Battery By [Reporter](/author/reporter/)"
            + " - Published On: 24 September 2026 Highlights"
            + " - The phone goes on sale next month."
            + " - It has a larger battery."
            + " The company unveiled the phone today with a larger battery."
            + " It will go on sale next month."
            + " Table of Contents - [Price](#Price) - [Specifications](#Specifications)"
            + " ## Price The phone starts at Rs 12,999 and comes in two colours."
            + " ## Specifications It has a high-refresh-rate display and a larger battery."
            + " [Share](https://www.facebook.com/sharer.php?u=example)"
            + " Related Articles [Another phone](https://example.com/related)"
        )
        title, summary, content = _extract_ai_article(page)
        self.assertEqual(title, "New Phone Launched With Larger Battery")
        self.assertEqual(summary, "The company unveiled the phone today with a larger battery.")
        self.assertTrue(content.startswith("The company unveiled"))
        self.assertIn("Specifications It has a high-refresh-rate display", content)
        self.assertNotIn("Table of Contents", content)
        self.assertNotIn("Related Articles", content)

    def test_video_description_after_share_toolbar(self):
        page = (
            " ".join(f"[Menu {index}](https://example.com/{index})" for index in range(8))
            + " # Official Calls for Action | Example Breaking News"
            + " - Share this Article - [ WhatsApp](https://example.com/share)"
            + " - copy link ![dropdown](https://example.com/icon.svg)"
            + " The official urged leaders to act quickly.Young people need support."
            + " -newsExample is your source for updates. Subscribe Now."
            + " **Last Updated: 24 September 2026** Advertisement"
        )
        title, summary, content = _extract_ai_article(page)
        self.assertEqual(title, "Official Calls for Action")
        self.assertEqual(summary, "The official urged leaders to act quickly.")
        self.assertEqual(content, "The official urged leaders to act quickly. Young people need support.")

    def test_hindi_article_stops_before_author_bio(self):
        page = (
            " ".join(f"[Menu {index}](https://example.com/{index})" for index in range(8))
            + " # त्योहार पर विशेष योग से कई राशियों को लाभ"
            + " Sep 24, 2026 01:44 pm IST By [Reporter](https://example.com/authors/reporter)"
            + " ## Festival horoscope : त्योहार पर विशेष योग बन रहा है।"
            + " कई राशियों को लाभ होगा। जानें किसे फायदा मिलेगा।"
            + " इस साल ग्रहों की स्थिति कई राशियों के लिए अनुकूल रहेगी।"
            + " ## किन राशियों को लाभ होगा इससे लोगों को नए अवसर मिल सकते हैं।"
            + " ये भी पढ़ें:[दूसरी खबर](https://example.com/related)"
            + " [ ## लेखक के बारे में **Reporter** लंबा परिचय और अन्य जानकारी"
        )
        title, summary, content = _extract_ai_article(page)
        self.assertEqual(title, "त्योहार पर विशेष योग से कई राशियों को लाभ")
        self.assertEqual(summary, "त्योहार पर विशेष योग बन रहा है। कई राशियों को लाभ होगा। जानें किसे फायदा मिलेगा।")
        self.assertTrue(content.startswith("त्योहार पर विशेष योग बन रहा है।"))
        self.assertNotIn("लंबा परिचय", content)
        self.assertNotIn("दूसरी खबर", content)

    def test_unidentifiable_page_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Could not identify article content"):
            _extract_ai_article("Advertisement " + " ".join(
                f"[Menu {index}](https://example.com/menu/{index})" for index in range(8)
            ))
        with self.assertRaisesRegex(ValueError, "Could not identify article content"):
            _extract_ai_article(" ".join(
                f"[Menu {index}](https://example.com/menu/{index})" for index in range(8)
            ))

    def test_structured_article_preserves_summary(self):
        title, summary, content = _extract_ai_article(json.dumps({
            "headline": "Council Debates New Voting Software",
            "summary": "Officials plan a review.",
            "content": "Officials opened a review of the voting software after several members raised concerns.",
        }))
        self.assertEqual(title, "Council Debates New Voting Software")
        self.assertEqual(summary, "Officials plan a review.")
        self.assertTrue(content.startswith("Officials opened"))

    def test_scraper_returns_parsed_fields(self):
        scraper = Scraper.__new__(Scraper)
        scraper._goose = Mock()
        with patch.dict("os.environ", {"SCRAPINGDOG_API_KEY": "test-key"}), patch(
            "allSearchAPI.app.scraping.requests.get", create=True
        ) as get:
            get.return_value = Mock(status_code=200, text=self.page)
            article = scraper.scrape("https://example.com/news/article")

        self.assertEqual(article.title, "Council Debates New Voting Software")
        self.assertEqual(article.summary, "Officials said the system will be reviewed next week.")
        self.assertTrue(article.text.startswith("New Delhi:"))
        self.assertNotIn("Related News", article.text)

    def test_json_ld_article_body_is_preferred(self):
        markup = """<html><head><script type="application/ld+json">{
            "@type": "NewsArticle",
            "url": "https://example.com/news/article",
            "headline": "Council Debates New Voting Software",
            "description": "Officials plan a review of the new system.",
            "articleBody": "Officials opened a review of the voting software after several members raised concerns about access to records."
        }</script></head><body><nav>Unrelated links</nav></body></html>"""
        article = _extract_html_article(markup, "https://example.com/news/article", Mock())
        self.assertEqual(article.title, "Council Debates New Voting Software")
        self.assertEqual(article.summary, "Officials plan a review of the new system.")
        self.assertTrue(article.text.startswith("Officials opened"))

    def test_article_dom_is_used_when_json_ld_has_no_body(self):
        markup = """<html><head><meta property="og:description" content="A new device is on sale."></head>
        <body><nav>Menu and latest stories</nav><article><h1>New Device Launches Today</h1>
        <p>The company launched a new device with a larger battery and a faster display.</p>
        <aside><p>A related story about another device.</p></aside>
        <h2>Availability</h2><p>The device will be sold next month through major stores.</p>
        </article><footer>More stories</footer></body></html>"""
        article = _extract_html_article(markup, "https://example.com/device", Mock())
        self.assertEqual(article.title, "New Device Launches Today")
        self.assertIn("Availability", article.text)
        self.assertNotIn("related story", article.text)
        self.assertNotIn("Menu", article.text)

    def test_video_description_is_not_promotional_boilerplate(self):
        markup = """<html><head><script type="application/ld+json">{
            "@type": "VideoObject", "url": "https://example.com/videos/story",
            "name": "Official Calls for Action",
            "description": "The official urged leaders to act quickly.Young people need support. -newsExample is your source for updates. Subscribe Now."
        }</script></head><body></body></html>"""
        article = _extract_html_article(markup, "https://example.com/videos/story", Mock())
        self.assertEqual(article.title, "Official Calls for Action")
        self.assertEqual(article.summary, "The official urged leaders to act quickly.")
        self.assertNotIn("Subscribe Now", article.text)
        self.assertEqual(article.content_type, "video_description")

    def test_video_transcript_is_labeled_only_when_present(self):
        markup = """<html><head><script type="application/ld+json">{
            "@type": "VideoObject", "url": "https://example.com/videos/story",
            "name": "Official Calls for Action",
            "description": "The official discussed a proposed policy.",
            "transcript": "The official explained the policy in detail and answered several questions from the audience."
        }</script></head><body></body></html>"""
        article = _extract_html_article(markup, "https://example.com/videos/story", Mock())
        self.assertEqual(article.content_type, "video_transcript")
        self.assertIn("answered several questions", article.text)

    def test_scraper_prefers_valid_html_without_ai_request(self):
        markup = """<html><head><script type="application/ld+json">{
            "@type": "NewsArticle", "url": "https://example.com/news/article",
            "headline": "Council Debates New Voting Software",
            "description": "Officials plan a review of the new system.",
            "articleBody": "Officials opened a review of the voting software after several members raised concerns about access to records."
        }</script></head><body></body></html>"""
        scraper = Scraper.__new__(Scraper)
        scraper._goose = Mock()
        with patch.dict("os.environ", {"SCRAPINGDOG_API_KEY": "test-key"}), patch(
            "allSearchAPI.app.scraping.requests.get", create=True
        ) as get:
            get.return_value = Mock(status_code=200, text=markup)
            article = scraper.scrape("https://example.com/news/article")
        self.assertEqual(article.title, "Council Debates New Voting Software")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["params"]["formats"], "html")

    def test_scraper_uses_fuller_markdown_when_html_drops_sections(self):
        markup = """<html><body><article><h1>Council Debates New Voting Software</h1>
        <p>Officials opened a review of the voting software after several members raised concerns.</p>
        </article></body></html>"""
        scraper = Scraper.__new__(Scraper)
        scraper._goose = Mock()
        with patch.dict("os.environ", {"SCRAPINGDOG_API_KEY": "test-key"}), patch(
            "allSearchAPI.app.scraping.requests.get", create=True
        ) as get:
            get.side_effect = [
                Mock(status_code=200, text=markup),
                Mock(status_code=200, text=self.page),
            ]
            article = scraper.scrape("https://example.com/news/article")
        self.assertEqual(article.source, "markdown")
        self.assertIn("The council will publish its findings", article.text)
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
