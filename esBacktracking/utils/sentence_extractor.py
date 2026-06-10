#!/usr/bin/env python3
"""
Utility to extract sentences and find keyword context for detailSummary
"""

import re
import nltk
from typing import List, Optional, Dict, Any

# Download required NLTK data if not already present
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

def extract_sentences(text: str) -> List[str]:
    """
    Extract sentences from text blob/paragraph using punctuation marks.
    Handles real-world PostgreSQL text content by splitting on sentence endings.
    
    Args:
        text: Input text blob/paragraph to extract sentences from
        
    Returns:
        List of sentences
    """
    if not text or not text.strip():
        return []
    
    # Clean the text first - remove extra whitespace and normalize
    cleaned_text = re.sub(r'\s+', ' ', text.strip())
    
    # Split on sentence endings: period, exclamation mark, question mark
    # Use positive lookahead to keep the punctuation with the sentence
    sentences = re.split(r'(?<=[.!?])\s+', cleaned_text)
    
    # Clean up sentences (remove extra whitespace and filter out very short ones)
    cleaned_sentences = []
    for sentence in sentences:
        sentence = sentence.strip()
        if sentence and len(sentence) > 10:  # Filter out very short fragments
            cleaned_sentences.append(sentence)
    
    return cleaned_sentences

def find_keyword_context(sentences: List[str], keywords: str, sources: Dict[str, List[str]]) -> Optional[str]:
    """
    Find the context around keywords in sentences.
    
    Args:
        sentences: List of sentences from the content
        keywords: Comma-separated keywords that were matched
        sources: Dictionary mapping keywords to their source fields
        
    Returns:
        Context string with current + 1 previous + 1 next sentence, or None if not found
    """
    if not sentences or not keywords:
        return None
    
    # Parse keywords (split by comma and clean)
    keyword_list = [kw.strip() for kw in keywords.split(',') if kw.strip()]
    
    if not keyword_list:
        return None
    
    # Find the first keyword that appears in the sentences
    target_keyword = None
    target_sentence_index = None
    
    for keyword in keyword_list:
        # Case-insensitive search for the keyword
        for i, sentence in enumerate(sentences):
            if keyword.lower() in sentence.lower():
                target_keyword = keyword
                target_sentence_index = i
                break
        if target_sentence_index is not None:
            break
    
    if target_sentence_index is None:
        return None
    
    # Extract context: previous + current + next sentence
    start_index = max(0, target_sentence_index - 1)
    end_index = min(len(sentences), target_sentence_index + 2)
    
    context_sentences = sentences[start_index:end_index]
    
    # Join sentences with spaces
    context = ' '.join(context_sentences)
    
    return context

def create_detail_summary(content: str, keywords: str, sources: Dict[str, List[str]]) -> Optional[str]:
    """
    Create detailSummary by finding keyword context in content.
    
    Args:
        content: The full content text
        keywords: Comma-separated keywords that were matched
        sources: Dictionary mapping keywords to their source fields
        
    Returns:
        Context string with keyword and surrounding sentences, or None if not found
    """
    if not content or not keywords:
        return None
    
    # Extract sentences from content
    sentences = extract_sentences(content)
    
    if not sentences:
        return None
    
    # Find keyword context
    context = find_keyword_context(sentences, keywords, sources)
    
    return context

def create_detail_summary_for_tag(tag_data: Dict[str, Any], content: str) -> Optional[str]:
    """
    Create detailSummary for a tag based on tag data and content.
    
    Args:
        tag_data: Tag data dictionary containing KEYWORDS and SOURCES
        content: The full content text
        
    Returns:
        Context string with keyword and surrounding sentences, or None if not found
    """
    keywords = tag_data.get('KEYWORDS', '')
    sources = tag_data.get('SOURCES', {})
    
    return create_detail_summary(content, keywords, sources)

# Test function
def test_sentence_extractor():
    """Test the sentence extractor functionality with realistic paragraph content."""
    
    # Simulate real PostgreSQL text blob content
    test_content = """Major Vineet Kumar, the renowned cybersecurity expert and founder of CyberPeace Foundation, announced the launch of CyberQuest 2025 during a press conference held in New Delhi today. This groundbreaking initiative is part of CyberPeace Foundation's comprehensive Key Initiatives program aimed at promoting cybersecurity awareness among students and young professionals across India. The program will include various competitions, workshops, and training sessions designed to enhance digital literacy and cybersecurity skills. CyberPeace Foundation has been working on this ambitious project for several months, collaborating with leading technology companies and educational institutions. The announcement was made at a packed press conference attended by industry leaders, government officials, and media representatives. The initiative is expected to reach over 10,000 students in its first year and will be expanded to other countries in the coming years. This represents a significant step forward in the foundation's mission to create a safer digital environment for all citizens."""
    
    test_keywords = "CyberQuest 2025, Key Initiatives, CyberPeace Foundation"
    test_sources = {
        "CyberQuest 2025": ["headline"],
        "Key Initiatives": ["content"],
        "CyberPeace Foundation": ["content"]
    }
    
    print("Testing Sentence Extractor with Realistic Paragraph Content")
    print("=" * 60)
    print(f"Content (first 150 chars): {test_content[:150]}...")
    print(f"Keywords: {test_keywords}")
    print()
    
    # Extract sentences
    sentences = extract_sentences(test_content)
    print(f"Extracted {len(sentences)} sentences from paragraph:")
    print("Raw sentences (as they appear in content):")
    for i, sentence in enumerate(sentences):
        print(f"  [{i+1}] {sentence}")
    print()
    
    # Create detail summary
    detail_summary = create_detail_summary(test_content, test_keywords, test_sources)
    print(f"Detail Summary: {detail_summary}")
    print()
    
    # Test with different keywords
    print("Testing with different keyword:")
    detail_summary2 = create_detail_summary(test_content, "cybersecurity awareness", test_sources)
    print(f"Detail Summary for 'cybersecurity awareness': {detail_summary2}")
    
    return detail_summary

if __name__ == "__main__":
    test_sentence_extractor()
