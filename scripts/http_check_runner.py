import argparse
import hashlib
import json
import time
import requests
import sys
import ssl
import urllib.parse
import socket
from cryptography import x509
from cryptography.hazmat.backends import default_backend
import base64 # Ensure base64 is imported at the top level

def make_request(target_url):
    """Makes an HTTP GET request and returns details as a dictionary."""
    details = {
        "url": target_url,
        "status_code": None,
        "headers": {},
        "body_sha256": None,
        "response_time_ms": None,
        "error": None,
        "body": None,
        "body_base64": None, # For binary/undecodable bodies
        "ssl_certificate_pem": None,
        "ssl_certificate_error": None,
    }

    # Fetch SSL certificate
    parsed_url = None
    try:
        parsed_url = urllib.parse.urlparse(target_url)
        hostname = parsed_url.hostname
        port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)

        if parsed_url.scheme == 'https' and hostname:
            sock = None
            conn = None
            try:
                sock = socket.create_connection((hostname, port), timeout=10)
                # Create a context that does not verify certificates
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                
                conn = context.wrap_socket(sock, server_hostname=hostname)
                
                der_cert_bin = conn.getpeercert(True) # Get DER-encoded certificate
                
                if der_cert_bin:
                    # Store PEM format
                    details["ssl_certificate_pem"] = ssl.DER_cert_to_PEM_cert(der_cert_bin)
                    
                    # Removed detailed parsing; Go side will handle this from PEM.
                    # If PEM is available, any previous "parsing error" specific to details is irrelevant.
                    # However, if DER retrieval itself failed, ssl_certificate_error would be set below.

                else:
                    details["ssl_certificate_error"] = "Failed to retrieve DER certificate from peer."

            except ssl.SSLCertVerificationError as e: # This error might still occur if other SSL issues arise, 
                                                  # but not due to CA/hostname verification failure if context is set correctly.
                details["ssl_certificate_error"] = f"SSL certificate issue (despite verification off): {e}"
            except ssl.SSLError as e:
                details["ssl_certificate_error"] = f"SSL error during cert fetching: {e}"
            except socket.timeout:
                details["ssl_certificate_error"] = f"Timeout connecting or during SSL handshake for {hostname}:{port} for cert fetching"
            except ConnectionRefusedError:
                details["ssl_certificate_error"] = f"Connection refused for {hostname}:{port} during cert fetching"
            except socket.gaierror as e: 
                 details["ssl_certificate_error"] = f"Address-related error connecting to {hostname} for cert fetching: {e}"
            except OSError as e:
                details["ssl_certificate_error"] = f"Socket/OS error during SSL certificate fetching for {hostname}: {e}"
            except Exception as e:
                details["ssl_certificate_error"] = f"Unexpected error fetching SSL cert for {hostname}: {e}"
            finally:
                if conn:
                    conn.close()
                elif sock:
                    sock.close()
        elif parsed_url and parsed_url.scheme != 'https':
            details["ssl_certificate_error"] = "Not an HTTPS URL, no SSL certificate to fetch."
        elif parsed_url and not hostname:
             details["ssl_certificate_error"] = "Could not determine hostname from URL to fetch SSL certificate."
        else:
            details["ssl_certificate_error"] = "Invalid URL or could not parse for SSL certificate fetching."

    except urllib.error.URLError as e:
        details["ssl_certificate_error"] = f"URL parsing error: {e}"

    start_time = time.perf_counter()

    try:
        response = requests.get(target_url, timeout=30, allow_redirects=True, verify=False)
        details["status_code"] = response.status_code
        
        # Ensure headers are map[string][]string for Go unmarshalling
        parsed_headers = {}
        for key, value in response.headers.items():
            parsed_headers[key] = [value] # Wrap each header value in a list
        details["headers"] = parsed_headers
        
        body_bytes = response.content
        details["body_sha256"] = hashlib.sha256(body_bytes).hexdigest()
        
        if body_bytes:
            try:
                details["body"] = body_bytes.decode('utf-8', errors='replace')
            except UnicodeDecodeError:
                details["body"] = "Error decoding body as UTF-8"
                # Fallback to Base64 encoding of the body if decode fails
                try:
                    details["body_base64"] = base64.b64encode(body_bytes).decode('ascii')
                except Exception as b64e:
                    details["body_base64"] = f"Error base64 encoding body: {b64e}"
        else:
            details["body"] = ""

    except requests.exceptions.Timeout as e:
        details["error"] = f"Request timed out: {e}"
    except requests.exceptions.SSLError as e:
        details["error"] = f"SSL error during HTTP request: {e}"
    except requests.exceptions.RequestException as e:
        details["error"] = f"Request failed: {e}"
    except Exception as e:
        details["error"] = f"An unexpected error occurred during request: {e}"
    finally:
        end_time = time.perf_counter()
        details["response_time_ms"] = int((end_time - start_time) * 1000)

    return details

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Make an HTTP request and save results to JSON.")
    parser.add_argument("target_url", help="The URL to make the request to. Can be a single URL or comma-separated URLs.")
    parser.add_argument("--output_file", default="result.json", help="Path to save the JSON output.")
    args = parser.parse_args()

    urls_to_check = [url.strip() for url in args.target_url.split(',') if url.strip()]
    
    all_results = []

    if not urls_to_check:
        print("No URLs provided to check.", file=sys.stderr)
        try:
            with open(args.output_file, 'w') as f:
                json.dump(all_results, f, indent=4)
            print(f"Wrote empty results array to {args.output_file} as no URLs were provided.", file=sys.stderr)
        except IOError as e:
            print(f"Error writing empty results to output file {args.output_file}: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    print(f"Processing {len(urls_to_check)} URL(s): {urls_to_check}", file=sys.stderr)

    for url_to_check_single in urls_to_check:
        print(f"Making request to: {url_to_check_single}", file=sys.stderr)
        result_data = make_request(url_to_check_single)
        all_results.append(result_data)

    try:
        with open(args.output_file, 'w') as f:
            json.dump(all_results, f, indent=4)
        print(f"Successfully wrote results for {len(all_results)} URL(s) to {args.output_file}", file=sys.stderr)
    except IOError as e:
        print(f"Error writing to output file {args.output_file}: {e}")
        print("Results JSON (stdout fallback):")
        print(json.dumps(all_results, indent=4)) 