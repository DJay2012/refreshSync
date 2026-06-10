"""
Optimized version of tagger.py with performance improvements
"""
import re
from collections import defaultdict
from .Config import es, INDEX_NAME
from src.helpers.cache_helpers import get_tagging_cache

# Cached skip keywords set for faster lookup
SKIP_KEYWORDS_SET = {
    "The", "the", "is", "or", "and", "a", "an", "in", "on", "at", "to", "of", "for", 
    "with", "by", "from", "about", "as", "into", "like", "after", "over", "under", 
    "between", "through", "during", "before", "after", "above", "below", "up", 
    "down", "out", "off", "then", "but", "so", "yet", "nor", "M"
}

# Cached language mapping
LANGUAGE_MAPPING = {
    'Hindi': 'hi', 'English': 'en', 'Gujarati': 'gu', 'Telugu': 'te', 'Marathi': 'mr',
    'Punjabi': 'pa', 'Malayalam': 'ml', 'Kannada': 'kn', 'Bengali': 'bn', 'Tamil': 'ta',
    'Urdu': 'ur', 'Odia': 'or', 'Assamese': 'as', 'Maithili': 'mai', 'Dogri': 'doi',
    'Chinese': 'en', 'Vietnamese': 'en', 'hi': 'hi', 'en': 'en', 'gu': 'gu', 'te': 'te',
    'mr': 'mr', 'pa': 'pa', 'ml': 'ml', 'kn': 'kn', 'bn': 'bn', 'ta': 'ta', 'ur': 'ur',
    'or': 'or', 'as': 'as', 'mai': 'mai', 'doi': 'doi', 'zh': 'en', 'vi': 'en',
    'ENGLISH': 'en', 'HINDI': 'hi', 'GUJARATI': 'gu', 'TELUGU': 'te', 'MARATHI': 'mr',
    'PUNJABI': 'pa', 'MALAYALAM': 'ml', 'KANNADA': 'kn', 'BENGALI': 'bn', 'TAMIL': 'ta',
    'URDU': 'ur', 'ODIA': 'or', 'ASSAMESE': 'as', 'MAITHILI': 'mai', 'DOGRI': 'doi',
    'CHINESE': 'en', 'VIETNAMESE': 'en'
}

def check_keywords_fast(keywords):
    """Fast keyword checking using set intersection"""
    return bool(set(keywords) - SKIP_KEYWORDS_SET)

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

def extract_percolator_phrases_cached(query_obj, cache={}):
    """Extract percolator phrases with caching"""
    # Simple cache based on query structure
    query_str = str(query_obj)
    if query_str in cache:
        return cache[query_str]
    
    phrases = []
    if isinstance(query_obj, dict):
        for key, value in query_obj.items():
            if key == "match_phrase" and "content" in value:
                if "query" in value["content"]:
                    phrases.append(value["content"]["query"])
            elif isinstance(value, (dict, list)):
                phrases.extend(extract_percolator_phrases_cached(value, cache))
    elif isinstance(query_obj, list):
        for item in query_obj:
            phrases.extend(extract_percolator_phrases_cached(item, cache))
    
    cache[query_str] = phrases
    return phrases

def find_keyword_source_fast(keyword, content_parts):
    """Fast keyword source finding with pre-split content"""
    keyword_lower = keyword.lower()
    
    for source, content in content_parts.items():
        if content and keyword_lower in content.lower():
            return source
    
    return "unknown"

def process_highlights_optimized(highlighted_words, percolator_phrases):
    """Optimized highlight processing"""
    if not percolator_phrases:
        return highlighted_words
    
    processed = []
    used_words = set()
    highlighted_words_lower = {hw.lower(): hw for hw in highlighted_words}

    # Process phrases first (more specific)
    for phrase in percolator_phrases:
        phrase_words = phrase.split()
        if all(word.lower() in highlighted_words_lower for word in phrase_words):
            processed.append(phrase)
            for word in phrase_words:
                word_lower = word.lower()
                if word_lower in highlighted_words_lower:
                    used_words.add(highlighted_words_lower[word_lower])

    # Add remaining individual words
    for word in highlighted_words:
        if word not in used_words:
            processed.append(word)
            used_words.add(word)
    
    return processed

def Tag_optimized(headline, content, summary, language):
    """Optimized tagging function with caching and performance improvements"""
    try:
        # Normalize language with cached mapping
        lang_code = LANGUAGE_MAPPING.get(language, 'en')
        
        # Build languages to search
        languages_to_search = [lang_code]
        if lang_code != 'en':
            languages_to_search.append('en')
        
        # Prepare content
        headline = headline or ""
        content = content or ""
        summary = summary or ""
        
        final_searchable_content = f"{headline}\n{content}\n{summary}"
        
        # Pre-calculate content parts for faster source detection
        content_parts = {
            "headline": headline,
            "content": content,
            "summary": summary
        }
        
        document = {
            "content": final_searchable_content,
            "content_case_sensitive": final_searchable_content,
        }

        # Track text ranges (optimized calculation)
        headline_len = len(headline)
        content_len = len(content)
        
        ranges = {
            "headline": (0, headline_len),
            "content": (headline_len + 1, headline_len + 1 + content_len),
            "summary": (headline_len + content_len + 2, len(final_searchable_content))
        }

        all_response_arr = []
        
        # Get cache for company names
        cache = get_tagging_cache()
        
        # Search for each language
        for current_lang in languages_to_search:
            percolator_field = f"lang_{current_lang}"
            
            # Optimized search query with smaller response
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
                        "content": {"number_of_fragments": 0},
                        "content_case_sensitive": {"number_of_fragments": 0}
                    },
                    "pre_tags": ["<em>"],
                    "post_tags": ["</em>"]
                },
                "size": 500  # Increased from 100 to 500 for more results
            }

            response = es.search(index=INDEX_NAME, body=search_query)
            
            # Handle different response formats
            if hasattr(response, 'body'):
                hits = response.body.get('hits', {}).get('hits', [])
            else:
                hits = response.get('hits', {}).get('hits', [])

            # Batch process hits
            company_ids = [hit.get('_source', {}).get('companyId') for hit in hits if hit.get('_source', {}).get('companyId')]
            
            # Get company names from cache
            if company_ids:
                company_names = cache.get_company_names([str(cid) for cid in company_ids])
            else:
                company_names = {}

            for hit in hits:
                temp_object = {}
                temp_object['index_id'] = hit.get('_id')
                temp_object['search_language'] = current_lang
                highlighted_words_with_source = []

                # Process highlights or percolator phrases
                if "highlight" not in hit:
                    # Use percolator phrases
                    source = hit.get('_source', {})
                    percolator_field_data = source.get(percolator_field, {})
                    extracted_phrases = extract_percolator_phrases_cached(percolator_field_data)
                    
                    for phrase in extracted_phrases:
                        source_location = find_keyword_source_fast(phrase, content_parts)
                        if source_location != "unknown":
                            highlighted_words_with_source.append((phrase, source_location))
                else:
                    # Process highlights (optimized)
                    highlight_regex = re.compile(r'<em>(.*?)</em>')
                    
                    for field_name in ["content", "content_case_sensitive"]:
                        if field_name in hit.get("highlight", {}):
                            for fragment in hit["highlight"][field_name]:
                                for match in highlight_regex.finditer(fragment):
                                    word = match.group(1)
                                    abs_pos = final_searchable_content.find(word)
                                    
                                    # Fast range detection
                                    source = "unknown"
                                    if abs_pos != -1:
                                        for range_name, (start, end) in ranges.items():
                                            if start <= abs_pos < end:
                                                source = range_name
                                                break
                                    
                                    highlighted_words_with_source.append((word, source))

                if not highlighted_words_with_source:
                    continue

                # Special-char phrase gate (e.g. P&G, IL&FS)
                source = hit.get('_source', {})
                percolator_field_data = source.get(percolator_field, {})
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
                        source_loc = "content"
                        if abs_pos != -1:
                            for range_name, (start, end) in ranges.items():
                                if start <= abs_pos < end:
                                    source_loc = range_name
                                    break
                        highlighted_words_with_source.append((p, source_loc))
                    if not highlighted_words_with_source:
                        continue

                highlighted_words = [w for w, _ in highlighted_words_with_source]
                percolator_phrases = extract_percolator_phrases_cached(source.get(percolator_field, {}))
                
                # Optimized highlight processing
                if "highlight" in hit:
                    processed = process_highlights_optimized(highlighted_words, percolator_phrases)
                else:
                    processed = highlighted_words

                # Build highlight sources efficiently
                highlight_sources = []
                used = set()
                for ph in processed:
                    for word, src in highlighted_words_with_source:
                        if word.lower() in ph.lower() and (word, src) not in used:
                            highlight_sources.append({"keyword": ph, "source": src})
                            used.add((word, src))
                            break

                # Get company info from cache
                company_id = source.get('companyId', '')
                company_name = company_names.get(str(company_id), source.get('companyName', ''))
                
                temp_object['company'] = {
                    'companyId': company_id,
                    'companyName': company_name
                }
                temp_object['highlight_keyword'] = [h["keyword"] for h in highlight_sources]
                temp_object['highlight_sources'] = highlight_sources

                if check_keywords_fast(temp_object['highlight_keyword']):
                    all_response_arr.append(temp_object)

        return all_response_arr

    except Exception as err:
        print(f"Error in optimized tagging: {err}")
        import traceback
        traceback.print_exc()
        return []

def tag_article_optimized(article_id, headline, summary, content, language):
    """Optimized version of tag_article with caching"""
    lang_code = LANGUAGE_MAPPING.get(language, language)
    tag_results = Tag_optimized(headline, content, summary, lang_code)

    results = []
    if isinstance(tag_results, list):
        company_results = defaultdict(lambda: {
            "ARTICLEID": article_id,
            "COMPANYID": "",
            "COMPANYNAME": "",
            "KEYWORDS": set(),
            "SOURCES": defaultdict(set),
            "SEARCH_LANGUAGES": set()
        })

        for result in tag_results:
            company_info = result['company']
            company_id = company_info.get('companyId', '')
            company_name = company_info.get('companyName', '')
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
            del value["SEARCH_LANGUAGES"]
            results.append(value)

    return results