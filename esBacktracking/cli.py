"""
Simplified Command-line interface for esPreview system.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

from espreview import ESPreviewEngine, ESPreviewConfig, ESPreviewError, QueryValidationError

class ESPreviewCLI:
    """Simplified command-line interface for esPreview system."""
    
    def __init__(self):
        self.config = None
        self.engine = None
        self.logger = None
    
    def create_parser(self) -> argparse.ArgumentParser:
        """Create command-line argument parser."""
        parser = argparse.ArgumentParser(
            description="esPreview - Simplified Elasticsearch Query Preview System",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  # Simple boolean query
  espreview "technology AND innovation"
  
  # Company stored query
  espreview --company "CYBERPE865"
  
  # List available companies
  espreview --list-companies
  
  # Interactive mode
  espreview --interactive
  
  # Health check
  espreview --health
            """
        )
        
        # Query input options (mutually exclusive)
        query_group = parser.add_mutually_exclusive_group()
        query_group.add_argument(
            "query",
            nargs="?",
            help="Boolean query string (e.g., 'technology AND innovation')"
        )
        
        query_group.add_argument(
            "--file", "-f",
            help="Path to file containing query"
        )
        
        query_group.add_argument(
            "--interactive", "-i",
            action="store_true",
            help="Run in interactive mode"
        )
        
        query_group.add_argument(
            "--health",
            action="store_true",
            help="Perform system health check"
        )
        
        query_group.add_argument(
            "--company",
            help="Execute stored query for a company by ID"
        )
        
        query_group.add_argument(
            "--list-companies",
            action="store_true",
            help="List available companies"
        )
        
        query_group.add_argument(
            "--search-companies",
            help="Search for companies by name or ID"
        )
        
        # Search configuration
        parser.add_argument(
            "--indexes",
            nargs="+",
            help="Indexes to search (default: printarticleindex socialfeedindex)"
        )
        
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum results per index (default: 50)"
        )
        
        parser.add_argument(
            "--timeout",
            type=int,
            help="Query timeout in seconds (default: 30)"
        )
        
        parser.add_argument(
            "--language",
            default="en",
            help="Language for company queries (default: en)"
        )
        
        # Output options
        parser.add_argument(
            "--output", "-o",
            help="Output file path (default: stdout)"
        )
        
        parser.add_argument(
            "--format",
            choices=["json", "table", "summary", "ids"],
            default="json",
            help="Output format (default: json)"
        )
        
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print JSON output"
        )
        
        parser.add_argument(
            "--quiet", "-q",
            action="store_true",
            help="Suppress non-essential output"
        )
        
        parser.add_argument(
            "--verbose", "-v",
            action="count",
            default=0,
            help="Increase verbosity (use -vv for debug level)"
        )
        
        return parser
    
    def run(self, args: Optional[List[str]] = None) -> int:
        """Run the CLI application."""
        try:
            parser = self.create_parser()
            parsed_args = parser.parse_args(args)
            
            # Setup logging
            self._setup_logging(parsed_args)
            
            # Load configuration
            self.config = self._load_config(parsed_args)
            
            # Initialize engine
            self.engine = ESPreviewEngine(self.config)
            
            # Handle different modes
            if parsed_args.health:
                return self._handle_health_check(parsed_args)
            elif parsed_args.list_companies:
                return self._handle_list_companies(parsed_args)
            elif parsed_args.search_companies:
                return self._handle_search_companies(parsed_args)
            elif parsed_args.company:
                return self._handle_company_query(parsed_args)
            elif parsed_args.interactive:
                return self._handle_interactive_mode(parsed_args)
            elif parsed_args.file:
                return self._handle_file_query(parsed_args)
            elif parsed_args.query:
                return self._handle_single_query(parsed_args)
            else:
                parser.print_help()
                return 1
                
        except KeyboardInterrupt:
            if not getattr(parsed_args, 'quiet', False):
                print("\nOperation cancelled by user", file=sys.stderr)
            return 130
        except Exception as e:
            print(f"Error: {str(e)}", file=sys.stderr)
            if getattr(parsed_args, 'verbose', 0) > 1:
                import traceback
                traceback.print_exc()
            return 1
    
    def _setup_logging(self, args):
        """Setup logging based on command-line arguments."""
        if args.verbose >= 2:
            log_level = "DEBUG"
        elif args.verbose == 1:
            log_level = "INFO"
        elif args.quiet:
            log_level = "ERROR"
        else:
            log_level = "WARNING"
        
        logging.basicConfig(
            level=getattr(logging, log_level),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=sys.stderr
        )
        
        self.logger = logging.getLogger(__name__)
    
    def _load_config(self, args) -> ESPreviewConfig:
        """Load configuration from environment and command-line arguments."""
        config = ESPreviewConfig.from_env()
        
        # Override with command-line arguments
        if args.limit is not None:
            config.max_results_per_index = args.limit
        
        if args.timeout is not None:
            config.timeout_seconds = args.timeout
        
        if args.indexes:
            config.target_indexes = args.indexes
        
        return config
    
    def _handle_health_check(self, args) -> int:
        """Handle health check command."""
        try:
            health_status = self.engine.health_check()
            
            if args.format == "json":
                output = json.dumps(health_status, indent=2 if args.pretty else None)
            else:
                status = health_status["status"]
                output = f"System Status: {status.upper()}\n"
                output += f"Elasticsearch: {health_status['elasticsearch']['status']}\n"
                output += f"Configuration: {health_status['configuration']['status']}\n"
                
                if health_status["errors"]:
                    output += f"Errors: {', '.join(health_status['errors'])}\n"
            
            self._write_output(output, args.output)
            return 0 if health_status["status"] == "healthy" else 1
            
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")
            return 1
    
    def _handle_interactive_mode(self, args) -> int:
        """Handle interactive mode for query testing."""
        if not args.quiet:
            print("esPreview Interactive Mode")
            print("Enter queries (boolean strings or DSL JSON)")
            print("Commands: :help, :config, :quit")
            print("-" * 50)
        
        try:
            while True:
                try:
                    query = input("espreview> ").strip()
                    
                    if not query:
                        continue
                    
                    # Handle special commands
                    if query.startswith(':'):
                        if query == ':quit' or query == ':q':
                            break
                        elif query == ':help' or query == ':h':
                            self._show_interactive_help()
                            continue
                        elif query == ':config':
                            self._show_config()
                            continue
                        else:
                            print(f"Unknown command: {query}")
                            continue
                    
                    # Execute query
                    result = self.engine.execute_query(query, args.indexes)
                    
                    # Format and display results
                    if args.format == "summary":
                        output = self._format_summary(result)
                    else:
                        output = json.dumps(self._result_to_dict(result), indent=2)
                    
                    print(output)
                    
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print("\nUse :quit to exit")
                    continue
                except Exception as e:
                    print(f"Error: {str(e)}")
                    continue
            
            if not args.quiet:
                print("Goodbye!")
            
            return 0
            
        except Exception as e:
            self.logger.error(f"Interactive mode failed: {e}")
            return 1
    
    def _handle_file_query(self, args) -> int:
        """Handle query from file."""
        try:
            file_path = Path(args.file)
            if not file_path.exists():
                raise FileNotFoundError(f"Query file not found: {args.file}")
            
            query_content = file_path.read_text().strip()
            
            # Execute query
            result = self.engine.execute_query(query_content, args.indexes)
            
            # Format and output results
            output = self._format_result(result, args.format, args.pretty)
            self._write_output(output, args.output)
            
            return 0 if result.success else 1
            
        except Exception as e:
            self.logger.error(f"File query failed: {e}")
            return 1
    
    def _handle_single_query(self, args) -> int:
        """Handle single query from command line."""
        try:
            # Execute query
            result = self.engine.execute_query(args.query, args.indexes)
            
            # Format and output results
            output = self._format_result(result, args.format, args.pretty)
            self._write_output(output, args.output)
            
            return 0 if result.success else 1
            
        except Exception as e:
            self.logger.error(f"Query execution failed: {e}")
            return 1
    
    def _handle_company_query(self, args) -> int:
        """Handle company query execution."""
        try:
            # Execute company query
            result = self.engine.execute_company_query(
                args.company, 
                language=args.language,
                indexes=args.indexes
            )
            
            # Format and output results
            output = self._format_result(result, args.format, args.pretty)
            self._write_output(output, args.output)
            
            return 0 if result.success else 1
            
        except Exception as e:
            self.logger.error(f"Company query execution failed: {e}")
            return 1
    
    def _handle_list_companies(self, args) -> int:
        """Handle list companies command."""
        try:
            companies = self.engine.list_companies(limit=args.limit or 100)
            
            if args.format == "json":
                output = json.dumps(companies, indent=2 if args.pretty else None)
            else:
                # Table format
                output = f"{'Company ID':<15} {'Company Name'}\n"
                output += "-" * 60 + "\n"
                for company in companies:
                    company_id = company['companyId'] or 'N/A'
                    company_name = company['companyName'] or 'N/A'
                    output += f"{company_id:<15} {company_name}\n"
                
                output += f"\nTotal: {len(companies)} companies"
            
            self._write_output(output, args.output)
            return 0
            
        except Exception as e:
            self.logger.error(f"List companies failed: {e}")
            return 1
    
    def _handle_search_companies(self, args) -> int:
        """Handle search companies command."""
        try:
            companies = self.engine.search_companies(args.search_companies, limit=args.limit or 20)
            
            if args.format == "json":
                output = json.dumps(companies, indent=2 if args.pretty else None)
            else:
                # Table format with scores
                output = f"{'Company ID':<15} {'Score':<8} {'Company Name'}\n"
                output += "-" * 70 + "\n"
                for company in companies:
                    company_id = company['companyId'] or 'N/A'
                    company_name = company['companyName'] or 'N/A'
                    score = f"{company.get('score', 0.0):.2f}"
                    output += f"{company_id:<15} {score:<8} {company_name}\n"
                
                output += f"\nFound: {len(companies)} companies matching '{args.search_companies}'"
            
            self._write_output(output, args.output)
            return 0
            
        except Exception as e:
            self.logger.error(f"Search companies failed: {e}")
            return 1
    
    def _format_result(self, result, format_type: str, pretty: bool = False) -> str:
        """Format result based on specified format."""
        if format_type == "summary":
            return self._format_summary(result)
        elif format_type == "table":
            return self._format_table(result)
        elif format_type == "ids":
            return self._format_ids(result)
        else:  # json
            return json.dumps(self._result_to_dict(result), indent=2 if pretty else None)
    
    def _format_summary(self, result) -> str:
        """Format result as human-readable summary."""
        output = []
        output.append(f"Query Results Summary")
        output.append(f"Execution Time: {result.execution_time_ms}ms")
        output.append(f"Total Matches: {result.total_matches}")
        
        if result.errors:
            output.append(f"Errors: {len(result.errors)}")
            for error in result.errors:
                output.append(f"  - {error}")
        
        output.append("")
        
        for index_name, index_result in result.index_results.items():
            output.append(f"Index: {index_name}")
            output.append(f"  Matches: {index_result.total_hits}")
            output.append(f"  Articles: {len(index_result.article_ids)}")
            
            if index_result.article_ids:
                output.append(f"  Sample IDs: {', '.join(index_result.article_ids[:5])}")
                if len(index_result.article_ids) > 5:
                    output.append(f"    ... and {len(index_result.article_ids) - 5} more")
            
            if index_result.errors:
                output.append(f"  Errors: {', '.join(index_result.errors)}")
            
            output.append("")
        
        return "\n".join(output)
    
    def _format_table(self, result) -> str:
        """Format result as table."""
        output = []
        output.append(f"{'Index':<20} {'Matches':<10} {'Time (ms)':<10} {'Status'}")
        output.append("-" * 60)
        
        for index_name, index_result in result.index_results.items():
            status = "ERROR" if index_result.errors else "OK"
            output.append(f"{index_name:<20} {index_result.total_hits:<10} {index_result.execution_time_ms:<10} {status}")
        
        output.append("")
        output.append(f"Total: {result.total_matches} matches in {result.execution_time_ms}ms")
        
        return "\n".join(output)
    
    def _format_ids(self, result) -> str:
        """Format result as simple list of article IDs."""
        output = []
        
        # Add header with query info
        if result.query_info:
            if result.query_info.get("query_type") == "company_stored_query":
                company_name = result.query_info.get("company_name", "Unknown")
                company_id = result.query_info.get("company_id", "Unknown")
                language = result.query_info.get("language", "en")
                output.append(f"# Company: {company_name} ({company_id}) - Language: {language}")
            else:
                original_input = result.query_info.get("original_input", "Unknown")
                output.append(f"# Query: {original_input}")
        
        output.append(f"# Total matches: {result.total_matches}")
        output.append(f"# Execution time: {result.execution_time_ms}ms")
        output.append("")
        
        # List all article IDs by index
        for index_name, index_result in result.index_results.items():
            if index_result.article_ids:
                output.append(f"# {index_name} ({index_result.total_hits} total hits, {len(index_result.article_ids)} returned)")
                for article_id in index_result.article_ids:
                    output.append(article_id)
                output.append("")
        
        return "\n".join(output)
    
    def _result_to_dict(self, result) -> Dict[str, Any]:
        """Convert ESPreviewResult to dictionary for JSON serialization."""
        return {
            "success": result.success,
            "total_matches": result.total_matches,
            "execution_time_ms": result.execution_time_ms,
            "query_info": result.query_info,
            "index_results": {
                name: {
                    "total_hits": idx_result.total_hits,
                    "article_ids": idx_result.article_ids,
                    "execution_time_ms": idx_result.execution_time_ms,
                    "field_matches": {
                        aid: {
                            "matched_fields": fm.matched_fields,
                            "score": fm.score,
                            "highlights": fm.highlights
                        } for aid, fm in idx_result.field_matches.items()
                    },
                    "errors": idx_result.errors
                } for name, idx_result in result.index_results.items()
            },
            "errors": result.errors
        }
    
    def _write_output(self, content: str, output_file: Optional[str]):
        """Write content to file or stdout."""
        if output_file:
            Path(output_file).write_text(content)
        else:
            print(content)
    
    def _show_interactive_help(self):
        """Show help for interactive mode."""
        help_text = """
Interactive Mode Commands:
  :help, :h     - Show this help
  :config       - Show current configuration
  :quit, :q     - Exit interactive mode
  
Query Examples:
  technology AND innovation
  "machine learning" OR "artificial intelligence"
  startup AND NOT failure
  {"bool": {"must": [{"match": {"headline": "tech"}}]}}
        """
        print(help_text.strip())
    
    def _show_config(self):
        """Show current configuration in interactive mode."""
        config_info = f"""
Current Configuration:
  Max Results per Index: {self.config.max_results_per_index}
  Timeout: {self.config.timeout_seconds}s
  Target Indexes: {', '.join(self.config.target_indexes)}
  Search Fields: {', '.join(self.config.search_fields)}
  Parallel Search: {'Enabled' if self.config.parallel_search else 'Disabled'}
        """
        print(config_info.strip())

def main():
    """Main entry point for CLI."""
    cli = ESPreviewCLI()
    return cli.run()

if __name__ == "__main__":
    sys.exit(main())
















