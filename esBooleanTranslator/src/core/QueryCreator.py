import json
import re
import pandas as pd
import csv
import sys
import os

# Try to import AddValuesIntoIndex with fallback
try:
    from AddValuesIntoIndex import insert_data_to_es_single
except ImportError:
    try:
        # Try relative import
        from ..inserters.AddValuesIntoIndex import insert_data_to_es_single
    except ImportError:
        # Try absolute import by adding src to path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        src_dir = os.path.join(current_dir, '..')
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        try:
            from inserters.AddValuesIntoIndex import insert_data_to_es_single
        except ImportError:
            # If all else fails, define a dummy function
            def insert_data_to_es_single(*args, **kwargs):
                print("Warning: AddValuesIntoIndex not available, using dummy function")
                return False

token_pattern = re.compile(
    r'(\+\+)"([^"]+)"|(\+)"([^"]+)"|"([^"]+)"|([()])|(NEAR)(?:/|\s*)?(\d+)?|(AND|OR|NOT)|([^()\s]+)'
)


def tokenize(query):
    tokens = []
    for match in token_pattern.finditer(query):
        groups = match.groups()
        if groups[0] is not None:  # ++"string"
            tokens.append(('CASE_SENSITIVE', groups[1].strip()))
        elif groups[2] is not None:  # +"string"
            tokens.append(('CASE_SENSITIVE', groups[3].strip()))
        elif groups[4] is not None:  # "string"
            tokens.append(('PHRASE', groups[4].strip()))
        elif groups[5] is not None:  # Parenthesis
            tokens.append((groups[5],))
        elif groups[6] is not None:  # NEAR
            slop = groups[7]
            if slop is None:
                slop = 10  # Default slop if not specified
            tokens.append(('NEAR', int(slop)))
        elif groups[8] is not None:  # AND, OR, NOT
            tokens.append((groups[8],))
        elif groups[9] is not None:  # Unquoted term
            term = groups[9].strip()
            # Skip standalone ++ tokens as they're malformed
            if term != '++':
                tokens.append(('TERM', term))
    
    # Fix unmatched parentheses
    tokens = fix_parentheses(tokens)
    return tokens

def fix_parentheses(tokens):
    """Fix unmatched parentheses by adding missing closing parentheses"""
    open_count = 0
    fixed_tokens = []
    
    for token in tokens:
        if token[0] == '(':
            open_count += 1
        elif token[0] == ')':
            open_count -= 1
        fixed_tokens.append(token)
    
    # Add missing closing parentheses at the end
    while open_count > 0:
        fixed_tokens.append((')',))
        open_count -= 1
    
    return fixed_tokens

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
    
    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None
    
    def consume(self):
        if self.pos < len(self.tokens):
            self.pos += 1
    
    def parse_expression(self):
        # Skip leading ORs
        while self.peek() and self.peek()[0] == 'OR':
            self.consume()
            
        if not self.peek():
            raise ValueError("Unexpected end of input")
            
        nodes = [self.parse_and_expression()]
        while self.peek() and self.peek()[0] == 'OR':
            self.consume()
            # Handle trailing OR
            if not self.peek():
                break
            nodes.append(self.parse_and_expression())
        return OrNode(nodes) if len(nodes) > 1 else nodes[0]
    
    def parse_and_expression(self):
        # Skip leading ANDs
        while self.peek() and self.peek()[0] == 'AND':
            self.consume()
            
        if not self.peek():
             raise ValueError("Unexpected end of input")

        nodes = [self.parse_not_expression()]
        while self.peek():
            token_type = self.peek()[0]
            if token_type == 'AND':
                self.consume()
                # Handle trailing AND
                if not self.peek():
                    break
                nodes.append(self.parse_not_expression())
            elif token_type == 'NOT':
                # Handle implicit AND before NOT
                self.consume()
                if not self.peek():
                    break
                not_node = self.parse_near_expression()
                nodes.append(NotNode(not_node))
            elif token_type in ('TERM', 'PHRASE', 'CASE_SENSITIVE', '('):
                # Implicit ANDs are not allowed - raise error
                raise ValueError(f"Missing operator before {self.peek()[1] if len(self.peek()) > 1 else token_type}")
            else:
                break
        return AndNode(nodes) if len(nodes) > 1 else nodes[0]
    
    def parse_not_expression(self):
        if self.peek() and self.peek()[0] == 'NOT':
            self.consume()
            node = self.parse_near_expression()
            return NotNode(node)
        else:
            return self.parse_near_expression()
    
    def parse_near_expression(self):
        node = self.parse_primary()
        while self.peek() and self.peek()[0] == 'NEAR':
            slop = self.peek()[1]
            self.consume()
            right_node = self.parse_primary()
            node = NearNode(node, right_node, slop)
        return node
    
    def parse_primary(self):
        token = self.peek()
        if not token:
            raise ValueError("Unexpected end of input")
        if token[0] == '(':
            self.consume()
            node = self.parse_expression()
            if self.peek() and self.peek()[0] == ')':
                self.consume()
            else:
                # Missing closing parenthesis - this should now be fixed by fix_parentheses()
                # But if somehow we still get here, just continue without the closing paren
                pass
            return node
        elif token[0] in ('CASE_SENSITIVE', 'PHRASE', 'TERM'):
            self.consume()
            case_sensitive = token[0] == 'CASE_SENSITIVE'
            value = token[1]
            return TermNode(value, case_sensitive)
        else:
            raise ValueError(f"Unexpected token: {token}")

class OrNode:
    def __init__(self, children):
        self.children = children

class AndNode:
    def __init__(self, children):
        self.children = children

class NotNode:
    def __init__(self, child):
        self.child = child

class NearNode:
    def __init__(self, left, right, slop):
        self.left = left
        self.right = right
        self.slop = slop

class TermNode:
    def __init__(self, value, case_sensitive=False):
        self.value = value
        self.case_sensitive = case_sensitive

def convert_node(node):
    raw = _convert_node(node)
    result = flatten_bool_clauses(raw)
    
    # Ensure single term queries are wrapped in bool/must
    if "bool" not in result:
        return {"bool": {"must": [result]}}
        
    return result

def _convert_node(node):
    if isinstance(node, OrNode):
        return {"bool": {"should": [_convert_node(child) for child in node.children]}}
    elif isinstance(node, AndNode):
        return {"bool": {"must": [_convert_node(child) for child in node.children]}}
    elif isinstance(node, NotNode):
        return {"bool": {"must_not": [_convert_node(node.child)]}}
    elif isinstance(node, NearNode):
        return convert_near_node(node)
    elif isinstance(node, TermNode):
        field = "content_case_sensitive" if node.case_sensitive else "content"
        return {"match_phrase": {field: {"query": node.value}}}
    else:
        raise ValueError(f"Unknown node type: {type(node)}")

def flatten_bool_clauses(query):
    """
    Flatten any nested must_not inside must to top-level must_not.
    """
    if not isinstance(query, dict) or 'bool' not in query:
        return query

    bool_query = query['bool']
    must = []
    must_not = []
    should = []

    # Unwrap nested must_not in must
    for clause in bool_query.get("must", []):
        if isinstance(clause, dict) and 'bool' in clause and 'must_not' in clause['bool']:
            must_not.extend(clause['bool']['must_not'])
        else:
            must.append(clause)

    # Add existing top-level must_not
    must_not.extend(bool_query.get("must_not", []))
    should.extend(bool_query.get("should", []))

    result = {}
    if must:
        result["must"] = must
    if must_not:
        result["must_not"] = must_not
    if should:
        result["should"] = should

    return {"bool": result}

def convert_near_node(near_node):
    slop = near_node.slop
    left = near_node.left
    right = near_node.right

    left_phrases = collect_phrases(left)
    right_phrases = collect_phrases(right)

    should_clauses = []
    for l_phrase in left_phrases:
        for r_phrase in right_phrases:
            combined = f"{l_phrase} {r_phrase}".strip()
            should_clauses.append({
                "match_phrase": {
                    "content": {
                        "query": combined,
                        "slop": slop
                    }
                }
            })
    return {"bool": {"should": should_clauses}} if len(should_clauses) > 1 else should_clauses[0]

def collect_phrases(node):
    if isinstance(node, TermNode):
        return [node.value]
    elif isinstance(node, OrNode):
        phrases = []
        for child in node.children:
            phrases.extend(collect_phrases(child))
        return phrases
    elif isinstance(node, AndNode):
        phrases = []
        for child in node.children:
            phrases.extend(collect_phrases(child))
        return [' '.join(phrases)]
    elif isinstance(node, NearNode):
        left = collect_phrases(node.left)
        right = collect_phrases(node.right)
        return [f"{l} {r}" for l in left for r in right]
    elif isinstance(node, NotNode):
        return []
    else:
        raise ValueError(f"Unsupported node type: {type(node)}")

def main():
    # file_path = "reseachenglishbooleanNewUpdatedlatestNew.xlsx"
    # file_path = "Copy of 85 of 128 corrected booleans.xlsx"
    # file_path = "newTransformedBoolean.xlsx"
    # file_path = "amazoneNewbooleanV2.xlsx"
    # file_path = "backtracking-14-04-25test.xlsxAllLanguage.xlsx"
    #file_path = "AmazoneAllBooleanAllLanguage.xlsx"
    # file_path = "AMZONEDATA2.xlsx"
    # file_path = "testamazone.xlsx"
    file_path = "New.xlsx"
    
    
    # file_path = "Transformed_Mar2_All_English_Excluding_Research_Company_Extraction (1) (1).xlsx"
    
    
    df = pd.read_excel(file_path)
    mainCompanyIndex = None
    tempFinalIndex = {}
    for index, row in df.iterrows():
        tempFinalIndex = {
            "companyId" :row.to_dict()['ID'],
            "companyName":row.to_dict()['Name'],
            "boolean":row.to_dict()['Boolean'],
            "lang":row.to_dict()['Language']
        }
        lang_precolator = 'lang_'+ row.to_dict()['Language']
        tokens = tokenize(row.to_dict()['Boolean'])
        parser = Parser(tokens)
        try:
            ast = parser.parse_expression()
            dsl = convert_node(ast)
            finalIndex = {
                "companyId": row.to_dict()['ID'],
                "companyName": row.to_dict()['Name'],
                lang_precolator: dsl,
            }
            if mainCompanyIndex is not None and finalIndex['companyId'] == mainCompanyIndex["companyId"]:
                mainCompanyIndex[lang_precolator] = finalIndex[lang_precolator]
            else: 
                if mainCompanyIndex is not None:
                    insert_data_to_es_single(mainCompanyIndex)
                mainCompanyIndex = finalIndex
                # print("Inserting data into ES:", mainCompanyIndex)
            
        except Exception as e:
            with open("AMZONEDATA2AllLANGerorprod.csv", mode="a", newline="", encoding='utf-8') as file:  # Change "w" to "a"
                writer = csv.DictWriter(file, fieldnames=['companyId', 'companyName', 'boolean', 'lang', 'error'])
                file.seek(0)  
                if file.tell() == 0:  
                    writer.writeheader()
                
                tempFinalIndex['error'] = str(e) 
                writer.writerow(tempFinalIndex)
            # print(tempFinalIndex)
            print(f"Error: {e}")
    
    # Insert the last company after the loop ends
    if mainCompanyIndex is not None:
        insert_data_to_es_single(mainCompanyIndex)

###TESTING CODE###
def test_parser():
    query = test_row['Boolean']
    tokens = tokenize(query)
    parser = Parser(tokens)
    ast = parser.parse_expression()
    dsl = convert_node(ast)
    print("\n==== Tokenized ====")
    print(tokens)
    print("\n==== Parsed DSL ====")
    print(json.dumps(dsl, indent=2))

test_row = {
    'ID': 'VISATEST',
    'Name': 'VISA Test Co',
    'Boolean': '(("VISA PAYWAVE" OR ("VISA" NEAR/10 ("DEBIT CARD" OR "CREDIT CARD")) OR "VISA SAFE CLICK" OR "PAY WITH VISA" OR ("VISA" NEAR/20 ("CONTACTLESS" OR "PAYMENT" OR "PAYMENTS" OR "CARD" OR "CARDS" OR "UPI" OR "WALLET" OR "SHAILESH PAUL" OR "ARVIND RONTA" OR "SUJAI RAINA" OR "VIPIN SURELIA" OR "SUJATHA V KUMAR" OR "RAMCHANDRA"))) NOT ("WATCHING" OR "OTT" OR "MOVIE" OR "TV SERIES" OR "DRAMA" OR "PLAY*" OR "STREAM*" OR "FILM" OR "TRAVEL*" OR "TOURIST*" OR "GREEN CARD" OR "IMMIGRATION" OR "RENEWAL" OR "EMPLOYMENT" OR "RESIDENT")) NOT "VISAS"',
    'Language': 'en'
}


if __name__ == "__main__":
    #test_parser()
    main()