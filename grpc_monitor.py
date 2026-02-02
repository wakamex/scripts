from scapy.all import sniff, IP, TCP
import datetime
import argparse
import sys

def analyze_packet(packet, port):
    """Analyze a single packet and extract relevant gRPC information."""
    if IP in packet and TCP in packet:
        # Extract source and destination information
        src_ip = packet[IP].src
        dst_ip = packet[IP].dst
        src_port = packet[TCP].sport
        dst_port = packet[TCP].dport
        
        # Only process packets to/from the specified port
        if src_port == port or dst_port == port:
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            direction = "→" if dst_port == port else "←"
            payload = packet[TCP].payload
            payload_hex = bytes(payload)[:50].hex() if payload else ""
            
            # Format and return the packet info
            return (f"[{timestamp}] "
                   f"{src_ip}:{src_port} {direction} {dst_ip}:{dst_port}\n"
                   f"Payload (first 50 bytes): {payload_hex}\n"
                   f"TCP Flags: {packet[TCP].flags}\n"
                   f"Length: {len(packet)}\n")
    return None

def monitor_traffic(port):
    """Main monitoring function."""
    print(f"Starting traffic monitor on port {port}...")
    print("Press Ctrl+C to stop monitoring\n")
    
    try:
        # Create capture filter for the specific port
        capture_filter = f"tcp port {port}"
        
        # Start packet capture
        sniff(filter=capture_filter,
              prn=lambda x: print(analyze_packet(x, port) or "", end=""),
              store=0)
    
    except KeyboardInterrupt:
        print("\nStopping monitor...")
        sys.exit(0)
    except PermissionError:
        print("Error: This script requires root/administrator privileges.")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Monitor TCP traffic on a specified port')
    parser.add_argument('port', type=int, help='Port number to monitor')
    parser.add_argument('--interface', '-i', help='Network interface to monitor (optional)')
    
    args = parser.parse_args()
    
    if args.port < 1 or args.port > 65535:
        print("Error: Port number must be between 1 and 65535")
        sys.exit(1)
    
    monitor_traffic(args.port)
