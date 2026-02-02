import socket
import concurrent.futures

def check_port(ip_address, port, timeout=1):
    """
    Checks if a specific port is open on the given IP address.
    Returns True if the port is open, False otherwise.
    """
    try:
        # Create a new socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Set a timeout for the connection attempt
        sock.settimeout(timeout)
        # Try to connect
        result = sock.connect_ex((ip_address, port))
        if result == 0:
            return True
        else:
            return False
    except socket.error as e:
        # print(f"Socket error for {ip_address}:{port} - {e}")
        return False
    finally:
        if 'sock' in locals():
            sock.close()

def scan_rtsp_ports(ip_address, common_ports=None, extended_ports=None, num_threads=10):
    """
    Scans a list of common and extended RTSP ports on the given IP address.
    """
    if common_ports is None:
        common_ports = [554]  # Standard RTSP port
    if extended_ports is None:
        extended_ports = [
            80,     # Sometimes RTSP can be tunneled over HTTP
            88,     # Kerberos, sometimes used with RTSP
            555,
            747,    # Often used by some cameras
            8080,   # Common alternative HTTP port
            8554,   # Common alternative RTSP port
            8555,
            8556,
            8557,
            8558,
            1024,   # Start of registered ports
            7070,   # Sometimes used for real-time streaming
            1935,   # RTMP, but sometimes cameras might listen here
            5000,
            5050,
            9000,
            9090
        ]

    ports_to_scan = list(set(common_ports + extended_ports)) # Combine and remove duplicates
    ports_to_scan.sort()

    open_ports = []
    closed_ports = 0
    total_ports = len(ports_to_scan)

    print(f"Scanning {ip_address} for potential RTSP ports...\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        future_to_port = {executor.submit(check_port, ip_address, port): port for port in ports_to_scan}
        for i, future in enumerate(concurrent.futures.as_completed(future_to_port)):
            port = future_to_port[future]
            try:
                if future.result():
                    print(f"Port {port} is OPEN")
                    open_ports.append(port)
                else:
                    closed_ports +=1
            except Exception as exc:
                print(f"Port {port} generated an exception: {exc}")
                closed_ports += 1
            
            # Simple progress indicator
            progress = (i + 1) / total_ports * 100
            print(f"\rProgress: {progress:.2f}% ({i+1}/{total_ports} ports checked)", end="")

    print("\n\n--- Scan Complete ---")
    if open_ports:
        print(f"Found open ports on {ip_address}: {open_ports}")
        print("Note: An open port doesn't guarantee an accessible RTSP stream.")
        print("You may still need the correct path, username, and password.")
    else:
        print(f"No common RTSP or alternative streaming ports found open on {ip_address} from the scanned list.")
    print(f"Checked {total_ports} ports. Open: {len(open_ports)}, Closed/Unreachable: {closed_ports}")


if __name__ == "__main__":
    target_ip = "192.168.0.198"  # <<-- REPLACE WITH YOUR CAMERA'S IP ADDRESS

    # You can customize the ports to scan if you have other ideas
    # common_ports_to_check = [554, 8554]
    # extended_ports_to_check = [80, 8080, 555, 7070]

    # scan_rtsp_ports(target_ip, common_ports_to_check, extended_ports_to_check)
    scan_rtsp_ports(target_ip, extended_ports=list(range(1,10001)))
