import re
from collections import defaultdict
from .Config import es, INDEX_NAME

# Words to skip when tagging keywords
skipKeywords = [
    "The", "the", "is", "or", "and", "a", "an", "in", "on", "at", "to", "of", "for", 
    "with", "by", "from", "about", "as", "into", "like", "after", "over", "under", 
    "between", "through", "during", "before", "after", "above", "below", "up", 
    "down", "out", "off", "then", "but", "so", "yet", "nor", "M"
]

language_mapping = {
    'Hindi': 'hi',
    'English': 'en',
    'Gujarati': 'gu',
    'Telugu': 'te',
    'Marathi': 'mr',
    'Punjabi': 'pa',
    'Malayalam': 'ml',
    'Kannada': 'kn',
    'Bengali': 'bn',
    'Tamil': 'ta',
    'Urdu': 'ur',
    'Odia': 'or',
    'Assamese': 'as',
    'Maithili': 'mai',
    'Dogri': 'doi',
    'Chinese': 'en',  # Fallback to English for Chinese
    'Vietnamese': 'en',  # Fallback to English for Vietnamese
    'hi': 'hi',
    'en': 'en',
    'gu': 'gu',
    'te': 'te',
    'mr': 'mr',
    'pa': 'pa',
    'ml': 'ml',
    'kn': 'kn',
    'bn': 'bn',
    'ta': 'ta',
    'ur': 'ur',
    'or': 'or',
    'as': 'as',
    'mai': 'mai',
    'doi': 'doi',
    'zh': 'en',  # Fallback to English for Chinese
    'vi': 'en',  # Fallback to English for Vietnamese
    'ENGLISH': 'en',
    'HINDI': 'hi',  
    'GUJARATI': 'gu',
    'TELUGU': 'te',
    'MARATHI': 'mr',
    'PUNJABI': 'pa',
    'MALAYALAM': 'ml',
    'KANNADA': 'kn',
    'BENGALI': 'bn',
    'TAMIL': 'ta',
    'URDU': 'ur',
    'ODIA': 'or',
    'ASSAMESE': 'as',
    'MAITHILI': 'mai',
    'DOGRI': 'doi',
    'CHINESE': 'en',  # Fallback to English for Chinese
    'VIETNAMESE': 'en',  # Fallback to English for Vietnamese
}

def check_keywords(skipKeywords, keywords):
    for keyword in keywords:
        if keyword not in skipKeywords:
            return True
    return False

HIGHLIGHT_REGEX = re.compile(r'<em>(.*?)</em>')
_SPECIAL_CHAR_RE = re.compile(r'[&@]')

def _extract_special_char_phrases(query_obj):
    """Extract phrases containing special chars (&, @) from a percolator query."""
    phrases = []
    if isinstance(query_obj, dict):
        for key, value in query_obj.items():
            if key == 'match_phrase':
                for field_val in value.values():
                    q = field_val.get('query', '') if isinstance(field_val, dict) else ''
                    if q and _SPECIAL_CHAR_RE.search(q):
                        phrases.append(q)
            elif isinstance(value, (dict, list)):
                phrases.extend(_extract_special_char_phrases(value))
    elif isinstance(query_obj, list):
        for item in query_obj:
            phrases.extend(_extract_special_char_phrases(item))
    return phrases

def extract_percolator_phrases(query_obj):
    phrases = []
    if isinstance(query_obj, dict):
        for key, value in query_obj.items():
            if key == "match_phrase" and "content" in value:
                if "query" in value["content"]:
                    phrases.append(value["content"]["query"])
            elif isinstance(value, (dict, list)):
                phrases.extend(extract_percolator_phrases(value))
    elif isinstance(query_obj, list):
        for item in query_obj:
            phrases.extend(extract_percolator_phrases(item))
    return phrases

def find_keyword_source(keyword, headline, content, summary):
    """Find which part of the text contains the keyword."""
    keyword_lower = keyword.lower()
    
    if headline and keyword_lower in headline.lower():
        return "headline"
    elif content and keyword_lower in content.lower():
        return "content"
    elif summary and keyword_lower in summary.lower():
        return "summary"
    else:
        return "unknown"

def process_hits_without_highlights(hit, percolator_field_name, headline, content, summary):
    """Process hits when no highlights are available, using the percolator query."""
    highlighted_words_with_source = []
    
    # Extract phrases from percolator query
    percolator_field_data = hit.get('_source', {}).get(percolator_field_name, {})
    extracted_phrases = extract_percolator_phrases(percolator_field_data)
    # print(f"Extracted phrases from percolator: {extracted_phrases[:5]}...")
    
    # Check each phrase against our content
    for phrase in extracted_phrases:
        source = find_keyword_source(phrase, headline, content, summary)
        if source != "unknown":
            highlighted_words_with_source.append((phrase, source))
    
    return highlighted_words_with_source

def process_highlights(highlighted_words, percolator_phrases):
    processed = []
    used_words = set()

    for phrase in percolator_phrases:
        phrase_words = phrase.split()
        if all(word.lower() in [hw.lower() for hw in highlighted_words] for word in phrase_words):
            processed.append(phrase)
            for word in phrase_words:
                for hw in highlighted_words:
                    if word.lower() == hw.lower():
                        used_words.add(hw)
                        break

    for word in highlighted_words:
        if word not in used_words:
            processed.append(word)
            used_words.add(word)
    
    return processed

def Tag(headline, content, summary, language):
    # print(f"Tagging: headline={headline[:30]}..., content={content[:30]}..., language={language}")
    try:
        # Normalize the incoming language to our supported code set.
        # If the language is unknown, we default to English (en).
        lang_code = language_mapping.get(language, 'en')
        
        # IMPORTANT: For non-English documents, also search the English percolator index.
        # Rationale:
        # - Many queries/keywords are maintained in English.
        # - Some languages intentionally fall back to English (see mapping for zh/vi).
        # - Articles can contain mixed-language text; checking English improves recall.
        # This builds the list of languages to query, e.g., ['hi', 'en'] for a Hindi article.
        languages_to_search = [lang_code]
        if lang_code != 'en':
            languages_to_search.append('en')
        
        headline = headline or ""
        content = content or ""
        summary = summary or ""

        final_searchable_content = f"{headline}\n{content}\n{summary}"
        document = {
            "content": final_searchable_content,
            "content_case_sensitive": final_searchable_content,
        }

        # Track text ranges
        headline_start = 0
        headline_end = len(headline)
        content_start = headline_end + 1
        content_end = content_start + len(content)
        summary_start = content_end + 1
        summary_end = summary_start + len(summary)

        all_response_arr = []
        
        # Search for each language
        for current_lang in languages_to_search:
            percolator_field = f"lang_{current_lang}"
            # Query the percolator field for each language (e.g., 'lang_en', 'lang_hi')
            
            search_query = {
                "query": {
                    "percolate": {
                        "field": percolator_field,
                        "document": document
                    }
                },
                "_source": ["companyId", "companyName", percolator_field],
                "highlight": {
                    "require_field_match": False,
                    "fields": {
                        "content": {
                            "number_of_fragments": 0
                        },
                        "content_case_sensitive": {
                            "number_of_fragments": 0
                        }
                    },
                    "pre_tags": ["<em>"],
                    "post_tags": ["</em>"]
                },
                "size": 500  # Increased from default 100 to 500 for more results
            }

            # Optimized: Silent execution for performance
            response = es.search(index=INDEX_NAME, body=search_query)
            
            # Handle different response formats from different elasticsearch client versions
            if hasattr(response, 'body'):
                hits = response.body.get('hits', {}).get('hits', [])
            else:
                hits = response.get('hits', {}).get('hits', [])

            for hit in hits:
                
                temp_object = {}
                temp_object['index_id'] = hit.get('_id')
                temp_object['search_language'] = current_lang  # Track which language found this hit
                highlighted_words_with_source = []

                # Check if highlight exists
                if "highlight" not in hit:
                    highlighted_words_with_source = process_hits_without_highlights(
                        hit, percolator_field, headline, content, summary
                    )
                else:
                    
                    def collect_highlights(field_name):
                        if field_name in hit.get("highlight", {}):
                            for fragment in hit["highlight"][field_name]:
                                for match in re.finditer(r'<em>(.*?)</em>', fragment):
                                    word = match.group(1)
                                    abs_pos = final_searchable_content.find(word)
                                    if abs_pos != -1:
                                        if headline_start <= abs_pos < headline_end:
                                            source = "headline"
                                        elif content_start <= abs_pos < content_end:
                                            source = "content"
                                        elif summary_start <= abs_pos < summary_end:
                                            source = "summary"
                                        else:
                                            source = "unknown"
                                    else:
                                        source = "unknown"
                                    highlighted_words_with_source.append((word, source))

                    collect_highlights("content")
                    collect_highlights("content_case_sensitive")

                # If no highlighted words found after attempts to get them, continue to next hit
                if not highlighted_words_with_source:
                    continue

                # Special-char phrase gate (e.g. P&G, IL&FS):
                # Remove broken tokens (<=2 chars from special-char phrase splits),
                # inject literally-matched special phrases, drop if nothing survives.
                percolator_field_data = hit.get('_source', {}).get(percolator_field, {})
                special_phrases = _extract_special_char_phrases(percolator_field_data)
                if special_phrases:
                    all_broken = set(
                        t for p in special_phrases
                        for t in p.replace('&', ' ').replace('@', ' ').split()
                        if len(t) <= 2
                    )
                    matched_special = [p for p in special_phrases if p in final_searchable_content]
                    highlighted_words_with_source = [
                        (w, s) for w, s in highlighted_words_with_source
                        if w not in all_broken
                    ]
                    for p in matched_special:
                        abs_pos = final_searchable_content.find(p)
                        if abs_pos != -1:
                            if headline_start <= abs_pos < headline_end:
                                sp_src = "headline"
                            elif content_start <= abs_pos < content_end:
                                sp_src = "content"
                            elif summary_start <= abs_pos < summary_end:
                                sp_src = "summary"
                            else:
                                sp_src = "content"
                        else:
                            sp_src = "content"
                        highlighted_words_with_source.append((p, sp_src))
                    if not highlighted_words_with_source:
                        continue

                highlighted_words = [w for w, _ in highlighted_words_with_source]
                source = hit.get('_source', {})
                percolator_phrases = extract_percolator_phrases(source.get(percolator_field, {}))
                
                # If we got highlighted words from the highlighting mechanism
                if "highlight" in hit:
                    processed = process_highlights(highlighted_words, percolator_phrases)
                else:
                    # We've already processed the phrases directly from the percolator
                    processed = highlighted_words

                highlight_sources = []
                used = set()
                for ph in processed:
                    for word, src in highlighted_words_with_source:
                        if word.lower() in ph.lower() and (word, src) not in used:
                            highlight_sources.append({"keyword": ph, "source": src})
                            used.add((word, src))
                            break

                temp_object['company'] = source
                temp_object['highlight_keyword'] = [h["keyword"] for h in highlight_sources]
                temp_object['highlight_sources'] = highlight_sources

                if check_keywords(skipKeywords, temp_object['highlight_keyword']):
                    all_response_arr.append(temp_object)

        return all_response_arr

    except Exception as err:
        print(f"Error in tagging: {err}")
        import traceback
        traceback.print_exc()
        return []

def tag_article(article_id, headline, summary, content, language):
    lang_code = language_mapping.get(language, language)
    tag_results = Tag(headline, content, summary, lang_code)

    results = []
    if isinstance(tag_results, list):
        company_results = defaultdict(lambda: {
            "ARTICLEID": article_id,
            "COMPANYID": "",
            "COMPANYNAME": "",
            "KEYWORDS": set(),
            "SOURCES": defaultdict(set),  # keyword -> set of sources
            "SEARCH_LANGUAGES": set()  # Track which languages found this company
        })

        for result in tag_results:
            company_id = result['company'].get('companyId', '')
            company_name = result['company'].get('companyName', '')
            search_lang = result.get('search_language', 'unknown')
            key = (company_id, company_name)

            company_results[key]["COMPANYID"] = company_id
            company_results[key]["COMPANYNAME"] = company_name
            company_results[key]["SEARCH_LANGUAGES"].add(search_lang)

            for keyword_info in result['highlight_sources']:
                keyword = keyword_info["keyword"]
                source = keyword_info["source"]
                company_results[key]["KEYWORDS"].add(keyword)
                company_results[key]["SOURCES"][keyword].add(source)

        for value in company_results.values():
            value["KEYWORDS"] = ", ".join(sorted(value["KEYWORDS"]))
            value["SOURCES"] = {k: list(v) for k, v in value["SOURCES"].items()}
            # Convert search languages to list for JSON serialization
            search_langs = list(value["SEARCH_LANGUAGES"])
            # Remove the SEARCH_LANGUAGES key as it's not needed in the final result
            del value["SEARCH_LANGUAGES"]
            results.append(value)

    return results