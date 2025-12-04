#!/bin/bash

# Zendesk MCP for Claude - Automated Installation Script (macOS Enterprise Edition)
# This script automates the installation and configuration of Zendesk MCP server for Claude on macOS
# with embedded enterprise certificates - no external files needed

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color

# Default installation directory
DEFAULT_INSTALL_DIR="$HOME/zendesk-mcp-server"

# Function to print colored output
print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_info() {
    echo -e "${PURPLE}[INFO]${NC} $1"
}

# Function to create enterprise certificate file
create_certificate_file() {
    local cert_path="$1"
    
    cat > "$cert_path" << 'CERT_EOF'
-----BEGIN CERTIFICATE-----
MIIC9zCCAd+gAwIBAgIFAIKWZUQwDQYJKoZIhvcNAQELBQAwKTEnMCUGA1UEAxMe
VGhpbmsgQmV5b25kIFB0ZS4gTHRkLiBSb290IENBMB4XDTI0MDkxOTA2NDcyNFoX
DTI2MDkxOTA2NDcyNFowMjEwMC4GA1UEAxMnVGhpbmsgQmV5b25kIFB0ZS4gTHRk
LiBGb3J3YXJkIFRydXN0IENBMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKC
AQEAzs9yoKLHNJxypiqboEcsO344oahxIlBn/vGJBp0HNKACn8OScXP9phdrWjOY
Tp8SF8fGZCN2oTIHl5XWhK2d7EuRs7FFUQ476yFpFYjj6axqYKPDpAU36uYdMre9
BSV28MOCwKS/HczWi9dJ1toHo/DoKVQg3TgGuzAiWuYVFG2cTpXI6V8ikXVidtV2
hKU4ilr+0tZip5MRcGBH8wGXwi1U3gNQwsXGh4INPHzfv5Ht8utdOjaCZulfshFl
6BELs3qz9p37hnlTuxayQ19MdMWZLgRKE5P9AAxpNdBDbOEqp9ujZ0yg6Ew+hCwR
1OdNB3dtNpGFsqH1yTPHb2aFiwIDAQABox0wGzAMBgNVHRMEBTADAQH/MAsGA1Ud
DwQEAwICBDANBgkqhkiG9w0BAQsFAAOCAQEAfn/89hhrGk0j/GE7uWaddv8Ng1f2
i7g2domQLDKVpXVqhmWd4dUKtEmW/8+w9YR051XfcVJbbNx/ch+cItPcOkdRs4Qo
kQPXkdl8a/LjF/NMhiKwKJOp8asolRc76jkjkZra1kjcP0n4GZ1lU6a0e79hpc+E
HzlA4DEbTruVdm8AstGxx8V8pLyMe5+onEid+6ik+bHeoHee9xBSoKjHoSE7MnwE
PS+9LautmMBG+lp/kMDjcDXp8zKoJGoyDg3SEMuK3ft4Ar2rFjsgJhYXiv2lPA/Y
EfTE4NAEfEm8BfcaJZLMc62ifyflpeafB27ibh/wG4YvnrcjWsWkOh3+0A==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIC8jCCAdqgAwIBAgIJANBqQU0XRBrVMA0GCSqGSIb3DQEBCwUAMCkxJzAlBgNV
BAMTHlRoaW5rIEJleW9uZCBQdGUuIEx0ZC4gUm9vdCBDQTAeFw0yNDA5MTkwNjQ3
MjNaFw0yNjA5MTkwNjQ3MjNaMCkxJzAlBgNVBAMTHlRoaW5rIEJleW9uZCBQdGUu
IEx0ZC4gUm9vdCBDQTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAK/e
S5aeuEedX0HCUzMgK8uM6cpMoZVg4xoWONjot2KaZg2WQnf5Y0Xl8f1Zk9knjcHm
quKrxA9u8GuiKtCKwVJ3UwUfG98Vv4V5ikRN26YNJfFssk/oXPGZSkOlO8bKsAo1
hiPt/LKeJaWD/Qxip5N/jEDCoZKTMoUHvBxi8NUd2fJQD52Httz+tHO85COUcBLq
5C9TxwFEcSdHxQC/OhiHbdbB9AdmTIVl1e5dlU11aO/d4VVsrUmaNuNKti2c3C9Y
u/dzBfBWjCIiJVmVChI4WOp7oHeZ2sxLuj5lQTuBRYb/owhwOgsRLbbCOncowmuE
RtgVmMyBSFNhmeBSypcCAwEAAaMdMBswDAYDVR0TBAUwAwEB/zALBgNVHQ8EBAMC
AgQwDQYJKoZIhvcNAQELBQADggEBAJEQK61iweokq3gkhOLYzyjJ6hXQNIiGuf/k
WJv2mPDM0bhHHEkWIyo0OG3K9vfI256U4dgTiNKG4t2IHvIucPw/pBZ7fzkqS1iu
/ttNlIAy/yducxekPGzGsfGhkXa2NFqWnIQfh8z2e5P6KXSAOXAq4HD78RakZZaO
WCSKLSITBamtJW1yJwK3tjQiDv5OlwMD+ZSTL45zkFWC3ymPAHyWv/Tub0KPWJ2x
/33zciTMp2HscREO/QiDlbcp3DmyZ652kbFnI2jg7s+tZb8Oo6g6QqMC4Jzy0yqu
LQBhc8PJkBvxz6cV6uOrgZIpHOwK4X7qvrjFzFyX0RDZ1vJK9+E=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICMjCCARqgAwIBAgIFAIKWZUYwDQYJKoZIhvcNAQELBQAwKTEnMCUGA1UEAxMe
VGhpbmsgQmV5b25kIFB0ZS4gTHRkLiBSb290IENBMB4XDTI0MDkxOTA2NDcyNVoX
DTI2MDkxOTA2NDcyNVowODE2MDQGA1UEAxMtVGhpbmsgQmV5b25kIFB0ZS4gTHRk
LiBGb3J3YXJkIFRydXN0IENBIEVDRFNBMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcD
QgAEhuVxl97M63bUxji8RwKTkD6ZCfI4npq/K1uP8pJye7YRtwESSalhn71AnfqE
Cn4GHlH5Q5g4pL2ne8Hv3AObjaMdMBswDAYDVR0TBAUwAwEB/zALBgNVHQ8EBAMC
AgQwDQYJKoZIhvcNAQELBQADggEBACK1+Gi4ZqCmBuYgVfgWT25BJtfqby4tXHFz
+lFEulDtEnIRqmU35VVzs7IVtjQ72Az6Lg8Hk9eAxvcuBFxwV1x4F1BTRK2m0wtG
xL3uQqhTyFF4OtnQ1WMrY1ABTFfyUPTvqQLv0lkXOYyC0lu+FTpI0cSBMOFCKzXN
qEUlvw2qqF5gXHUJTyG3nJo6JoFM86/+UgCbQUZocp1qct1MMBlbPPnw9T1222IC
kuLr3N7zmGTy22WOFEFxbpR95S0d/CrGx42Knzo+XGDGFN45sPp8EZ6uNfWvgM9S
GujNCLgiNiA+JQTOn0dysoXe9mD+DLYGsDY3v5u+lJPz3ljSak8=
-----END CERTIFICATE-----
CERT_EOF
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to get user input with default value
get_input() {
    local prompt="$1"
    local default="$2"
    local varname="$3"
    
    if [ -n "$default" ]; then
        read -p "$prompt [$default]: " input
        eval $varname="\${input:-$default}"
    else
        while [ -z "${!varname}" ]; do
            read -p "$prompt: " input
            eval $varname="$input"
            if [ -z "${!varname}" ]; then
                print_error "This field is required. Please enter a value."
            fi
        done
    fi
}

# Function to install Homebrew
install_homebrew() {
    print_step "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Add Homebrew to PATH for current session
    if [[ -f "/opt/homebrew/bin/brew" ]]; then
        # Apple Silicon Mac
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        # Intel Mac
        echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    
    print_success "Homebrew installed successfully"
}

# Function to backup existing config
backup_config() {
    local config_file="$1"
    if [ -f "$config_file" ]; then
        local backup_file="${config_file}.backup.$(date +%Y%m%d_%H%M%S)"
        cp "$config_file" "$backup_file"
        print_success "Backed up existing config to: $backup_file"
    fi
}

# Function to merge or create Claude config
update_claude_config() {
    local config_file="$1"
    local install_dir="$2"
    local uv_path="$3"
    local server_name="${4:-zendesk}"  # Default to "zendesk" if not provided

    # Create config directory if it doesn't exist
    mkdir -p "$(dirname "$config_file")"

    # Backup existing config
    backup_config "$config_file"

    # Check if config file exists and has content
    if [ -f "$config_file" ] && [ -s "$config_file" ]; then
        # File exists and is not empty, try to merge
        print_step "Merging with existing Claude configuration..."

        # Check if it's valid JSON
        if jq empty "$config_file" 2>/dev/null; then
            # Valid JSON, merge the zendesk server config
            jq --arg server_name "$server_name" --arg install_dir "$install_dir" --arg uv_path "$uv_path" '
                .mcpServers[$server_name] = {
                    "command": $uv_path,
                    "args": [
                        "--directory",
                        $install_dir,
                        "run",
                        "zendesk"
                    ]
                }
            ' "$config_file" > "${config_file}.tmp" && mv "${config_file}.tmp" "$config_file"
        else
            print_warning "Existing config is not valid JSON. Creating new configuration..."
            create_new_config "$config_file" "$install_dir" "$uv_path" "$server_name"
        fi
    else
        # File doesn't exist or is empty, create new
        create_new_config "$config_file" "$install_dir" "$uv_path" "$server_name"
    fi
}

# Function to create new Claude config
create_new_config() {
    local config_file="$1"
    local install_dir="$2"
    local uv_path="$3"
    local server_name="${4:-zendesk}"  # Default to "zendesk" if not provided

    cat > "$config_file" << EOF
{
    "mcpServers": {
        "$server_name": {
            "command": "$uv_path",
            "args": [
                "--directory",
                "$install_dir",
                "run",
                "zendesk"
            ]
        }
    }
}
EOF
}

# Function to install enterprise certificates
install_enterprise_certs() {
    local install_dir="$1"
    
    print_step "Installing enterprise certificates..."
    
    # Create temporary certificate file
    local temp_cert_file="$(mktemp)"
    create_certificate_file "$temp_cert_file"
    
    # Find the uv virtual environment for the project
    cd "$install_dir"
    
    # Create uv environment if it doesn't exist
    if [ ! -d ".venv" ]; then
        print_step "Creating Python virtual environment..."
        uv venv
    fi
    
    # Find Python's certifi certificate bundle
    local python_path
    python_path=$(uv run python -c "import sys; print(sys.executable)")
    local venv_path="$install_dir/.venv"
    
    # Find certifi's cacert.pem file
    local certifi_path
    certifi_path=$(uv run python -c "import certifi; print(certifi.where())" 2>/dev/null) || {
        print_step "Installing certifi package..."
        uv add certifi
        certifi_path=$(uv run python -c "import certifi; print(certifi.where())")
    }
    
    print_info "Found certifi bundle at: $certifi_path"
    
    # Backup original certificate bundle
    if [ ! -f "${certifi_path}.original" ]; then
        cp "$certifi_path" "${certifi_path}.original"
        print_success "Backed up original certificate bundle"
    fi
    
    # Append enterprise certificates to the bundle
    cat "$temp_cert_file" >> "$certifi_path"
    print_success "Enterprise certificates added to Python certificate bundle"
    
    # Also add to system keychain for broader compatibility
    print_step "Adding certificates to macOS keychain..."
    security add-trusted-cert -d -r trustRoot -k "/Library/Keychains/System.keychain" "$temp_cert_file" 2>/dev/null || {
        print_warning "Could not add to system keychain (requires admin privileges)"
        print_info "Adding to user keychain instead..."
        security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db "$temp_cert_file"
    }
    
    # Clean up temporary certificate file
    rm -f "$temp_cert_file"
    
    print_success "Enterprise certificates installed successfully"
}

# Function to detect existing Zendesk MCP installations
detect_existing_installations() {
    local config_file="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

    if [ ! -f "$config_file" ]; then
        echo ""
        return 0
    fi

    if ! jq empty "$config_file" 2>/dev/null; then
        echo ""
        return 0
    fi

    # Find all MCP servers with names starting with "zendesk"
    local zendesk_servers
    zendesk_servers=$(jq -r '.mcpServers | keys[] | select(startswith("zendesk"))' "$config_file" 2>/dev/null)

    # Return the list of server names (empty string if none found)
    echo "$zendesk_servers"
    return 0
}

# Function to extract installation info from a server name
extract_installation_info() {
    local server_name="$1"
    local config_file="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

    # Get installation directory from Claude config
    local install_dir
    install_dir=$(jq -r ".mcpServers.\"$server_name\".args[1]" "$config_file" 2>/dev/null)

    if [ -z "$install_dir" ] || [ "$install_dir" == "null" ]; then
        echo "||||"
        return 0
    fi

    # Check if .env exists in that directory
    if [ ! -f "$install_dir/.env" ]; then
        echo "$install_dir||||"
        return 0
    fi

    # Extract credentials from .env
    local subdomain email api_key
    subdomain=$(grep "^ZENDESK_SUBDOMAIN=" "$install_dir/.env" 2>/dev/null | cut -d'=' -f2)
    email=$(grep "^ZENDESK_EMAIL=" "$install_dir/.env" 2>/dev/null | cut -d'=' -f2)
    api_key=$(grep "^ZENDESK_API_KEY=" "$install_dir/.env" 2>/dev/null | cut -d'=' -f2)

    # Return pipe-separated values for easy parsing
    echo "$install_dir|$subdomain|$email|$api_key"
    return 0
}

# Function to display existing installations and get user choice
handle_existing_installations() {
    local existing_servers="$1"
    local server_count=$(echo "$existing_servers" | wc -l | tr -d ' ')

    echo ""
    echo "========================================================="
    print_info "Found $server_count existing Zendesk MCP installation(s):"
    echo "========================================================="
    echo ""

    local index=1
    local -a server_names
    local -a server_info

    while IFS= read -r server_name; do
        server_names+=("$server_name")
        local info=$(extract_installation_info "$server_name")
        server_info+=("$info")

        IFS='|' read -r install_dir subdomain email api_key <<< "$info"

        echo "[$index] Server name: $server_name"
        echo "    Directory: $install_dir"
        if [ -n "$subdomain" ]; then
            echo "    Subdomain: $subdomain"
            echo "    Email: $email"
        else
            echo "    Status: ⚠ Configuration not found"
        fi
        echo ""

        ((index++))
    done <<< "$existing_servers"

    echo "What would you like to do?"
    echo "[1] Update an existing installation (reuse credentials)"
    echo "[2] Update ALL installations (batch update)"
    echo "[3] Install a new instance (different subdomain/credentials)"
    echo "[4] Cancel"
    echo ""

    local choice
    read -p "Enter your choice [1-4]: " choice

    case $choice in
        1)
            if [ $server_count -eq 1 ]; then
                # Only one installation, use it directly
                local info="${server_info[0]}"
                IFS='|' read -r install_dir subdomain email api_key <<< "$info"

                if [ -z "$subdomain" ]; then
                    print_error "Cannot extract credentials from existing installation"
                    echo "Installation cancelled."
                    exit 0
                fi

                echo ""
                print_success "Will update installation: ${server_names[0]}"
                echo "  Directory: $install_dir"
                echo "  Subdomain: $subdomain"
                echo "  Email: $email"
                echo ""
                read -p "Continue with this configuration? (y/N): " confirm

                if [[ ! $confirm =~ ^[Yy]$ ]]; then
                    echo "Installation cancelled."
                    exit 0
                fi

                # Export values for use in main installation
                export UPDATE_MODE="true"
                export UPDATE_SERVER_NAME="${server_names[0]}"
                export ZENDESK_SUBDOMAIN="$subdomain"
                export ZENDESK_EMAIL="$email"
                export ZENDESK_API_KEY="$api_key"
                export INSTALL_DIR="$install_dir"
                return 0
            else
                # Multiple installations, ask which one to update
                echo ""
                read -p "Which installation do you want to update? [1-$server_count]: " install_choice

                if [[ ! "$install_choice" =~ ^[0-9]+$ ]] || [ "$install_choice" -lt 1 ] || [ "$install_choice" -gt "$server_count" ]; then
                    print_error "Invalid choice"
                    echo "Installation cancelled."
                    exit 0
                fi

                local selected_index=$((install_choice - 1))
                local info="${server_info[$selected_index]}"
                IFS='|' read -r install_dir subdomain email api_key <<< "$info"

                if [ -z "$subdomain" ]; then
                    print_error "Cannot extract credentials from selected installation"
                    return 1
                fi

                echo ""
                print_success "Will update installation: ${server_names[$selected_index]}"
                echo "  Directory: $install_dir"
                echo "  Subdomain: $subdomain"
                echo "  Email: $email"
                echo ""
                read -p "Continue with this configuration? (y/N): " confirm

                if [[ ! $confirm =~ ^[Yy]$ ]]; then
                    echo "Installation cancelled."
                    exit 0
                fi

                # Export values for use in main installation
                export UPDATE_MODE="true"
                export UPDATE_SERVER_NAME="${server_names[$selected_index]}"
                export ZENDESK_SUBDOMAIN="$subdomain"
                export ZENDESK_EMAIL="$email"
                export ZENDESK_API_KEY="$api_key"
                export INSTALL_DIR="$install_dir"
                return 0
            fi
            ;;
        2)
            # Update ALL installations
            echo ""
            print_info "Updating ALL $server_count installation(s)..."
            echo ""

            # Show summary of what will be updated
            local idx=1
            while IFS= read -r server_name; do
                local info="${server_info[$((idx-1))]}"
                IFS='|' read -r install_dir subdomain email api_key <<< "$info"
                echo "[$idx] $server_name (Subdomain: $subdomain)"
                ((idx++))
            done <<< "$existing_servers"

            echo ""
            read -p "Update all these installations? (y/N): " confirm

            if [[ ! $confirm =~ ^[Yy]$ ]]; then
                echo "Installation cancelled."
                exit 0
            fi

            # Set batch update mode and call update function directly
            update_all_installations_with_data "${server_names[@]}" "|||" "${server_info[@]}"
            exit 0
            ;;
        3)
            # New installation with different credentials
            echo ""
            print_info "Installing new instance alongside existing installation(s)..."
            export UPDATE_MODE="false"
            export NEW_INSTANCE="true"
            return 0
            ;;
        4)
            echo "Installation cancelled."
            exit 0
            ;;
        *)
            print_error "Invalid choice"
            echo "Installation cancelled."
            exit 0
            ;;
    esac
}

# Function to update all installations
update_all_installations_with_data() {
    print_step "Starting batch update of all installations..."
    echo ""

    # Parse arguments - split by separator "|||"
    local -a server_names
    local -a server_info
    local parsing_names=true

    for arg in "$@"; do
        if [ "$arg" == "|||" ]; then
            parsing_names=false
            continue
        fi
        if $parsing_names; then
            server_names+=("$arg")
        else
            server_info+=("$arg")
        fi
    done

    # Get uv path
    UV_PATH=$(which uv)
    CLAUDE_CONFIG_FILE="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

    local total_count=${#server_names[@]}
    local success_count=0
    local failed_count=0
    local -a failed_servers

    # Process each installation
    for i in "${!server_names[@]}"; do
        local server_name="${server_names[$i]}"
        local info="${server_info[$i]}"
        IFS='|' read -r install_dir subdomain email api_key <<< "$info"

        echo "========================================================="
        print_info "[$((i+1))/$total_count] Updating: $server_name"
        echo "  Directory: $install_dir"
        echo "  Subdomain: $subdomain"
        echo "========================================================="

        # Validate credentials exist
        if [ -z "$subdomain" ] || [ -z "$install_dir" ]; then
            print_error "Cannot extract credentials for $server_name, skipping..."
            ((failed_count++))
            failed_servers+=("$server_name (missing credentials)")
            echo ""
            continue
        fi

        # Create temporary directory for download
        local TEMP_DIR=$(mktemp -d)

        # Download source code
        print_step "Downloading latest code from GitHub..."
        cd "$TEMP_DIR"

        GITHUB_URL="https://github.com/lyb0307/zendesk-mcp-server/archive/refs/heads/main.zip"

        if ! curl -L -o zendesk-mcp.zip "$GITHUB_URL" 2>/dev/null; then
            print_error "Failed to download source code, skipping $server_name..."
            ((failed_count++))
            failed_servers+=("$server_name (download failed)")
            rm -rf "$TEMP_DIR"
            echo ""
            continue
        fi

        # Extract
        if ! unzip -q zendesk-mcp.zip 2>/dev/null; then
            print_error "Failed to extract source code, skipping $server_name..."
            ((failed_count++))
            failed_servers+=("$server_name (extraction failed)")
            rm -rf "$TEMP_DIR"
            echo ""
            continue
        fi

        SOURCE_DIR="$TEMP_DIR/zendesk-mcp-server-main"

        # Backup existing installation
        if [ -d "$install_dir" ]; then
            print_step "Backing up existing installation..."
            mv "$install_dir" "${install_dir}.backup.$(date +%Y%m%d_%H%M%S)"
        fi

        # Install new version
        print_step "Installing new version..."
        mkdir -p "$(dirname "$install_dir")"
        cp -r "$SOURCE_DIR" "$install_dir"
        cd "$install_dir"

        # Install certificates
        print_step "Installing enterprise certificates..."
        install_enterprise_certs "$install_dir" 2>&1 | grep -E "(SUCCESS|ERROR|WARNING)" || true

        # Build
        print_step "Building MCP server..."
        if ! uv build 2>&1 | tail -3; then
            print_warning "Build may have issues, but continuing..."
        fi

        # Restore .env with preserved credentials
        print_step "Restoring configuration..."
        cat > .env << EOF
ZENDESK_SUBDOMAIN=$subdomain
ZENDESK_EMAIL=$email
ZENDESK_API_KEY=$api_key
EOF

        # Update Claude config
        print_step "Updating Claude Desktop configuration..."
        update_claude_config "$CLAUDE_CONFIG_FILE" "$install_dir" "$UV_PATH" "$server_name"

        # Cleanup
        rm -rf "$TEMP_DIR"

        print_success "✅ Successfully updated: $server_name"
        ((success_count++))
        echo ""
    done

    # Summary
    echo ""
    echo "========================================================="
    echo "           Batch Update Summary"
    echo "========================================================="
    echo ""
    print_info "Total installations: $total_count"
    print_success "Successfully updated: $success_count"

    if [ $failed_count -gt 0 ]; then
        print_error "Failed: $failed_count"
        echo ""
        echo "Failed installations:"
        for failed in "${failed_servers[@]}"; do
            echo "  - $failed"
        done
    fi

    echo ""
    echo "========================================================="
    echo "                All Updates Complete!"
    echo "========================================================="
    echo ""
    print_warning "🚨 IMPORTANT: Restart Claude Desktop app to activate the updates!"
    echo ""
}

# Function to test the MCP server installation
test_installation() {
    local install_dir="$1"
    local zendesk_subdomain="$2"
    
    print_step "Testing Zendesk MCP server installation..."
    
    cd "$install_dir"
    
    # Test 1: Check if uv can run the server
    print_info "Test 1: Checking if MCP server starts..."
    timeout 10s uv run zendesk --help >/dev/null 2>&1 && {
        print_success "✓ MCP server executable is working"
    } || {
        print_warning "⚠ MCP server executable test failed"
    }
    
    # Test 2: Check environment configuration
    print_info "Test 2: Checking environment configuration..."
    if [ -f ".env" ]; then
        print_success "✓ Environment file exists"
        if grep -q "ZENDESK_SUBDOMAIN=" .env && grep -q "ZENDESK_EMAIL=" .env && grep -q "ZENDESK_API_KEY=" .env; then
            print_success "✓ All required environment variables are set"
        else
            print_warning "⚠ Some environment variables may be missing"
        fi
    else
        print_error "✗ Environment file is missing"
    fi
    
    # Test 3: Check certificate installation
    print_info "Test 3: Checking certificate installation..."
    local certifi_path
    certifi_path=$(uv run python -c "import certifi; print(certifi.where())" 2>/dev/null) && {
        if grep -q "Think Beyond" "$certifi_path" 2>/dev/null; then
            print_success "✓ Enterprise certificates are installed in Python bundle"
        else
            print_warning "⚠ Enterprise certificates not found in Python bundle"
        fi
    } || {
        print_warning "⚠ Could not check certificate installation"
    }
    
    # Test 4: Test network connectivity to Zendesk
    print_info "Test 4: Testing network connectivity to Zendesk..."
    if curl -s --max-time 10 "https://${zendesk_subdomain}.zendesk.com" >/dev/null 2>&1; then
        print_success "✓ Can reach Zendesk subdomain: ${zendesk_subdomain}.zendesk.com"
    else
        print_warning "⚠ Cannot reach Zendesk subdomain (might be network/proxy issue)"
    fi
    
    # Test 5: Check Claude config
    print_info "Test 5: Checking Claude Desktop configuration..."
    local claude_config="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
    if [ -f "$claude_config" ]; then
        if jq -e '.mcpServers.zendesk' "$claude_config" >/dev/null 2>&1; then
            print_success "✓ Claude Desktop configuration includes Zendesk MCP server"
        else
            print_warning "⚠ Zendesk MCP server not found in Claude configuration"
        fi
    else
        print_warning "⚠ Claude Desktop configuration file not found"
    fi
    
    echo ""
    print_info "=== DEBUGGING INFORMATION ==="
    echo "Installation Directory: $install_dir"
    echo "Python Virtual Environment: $install_dir/.venv"
    echo "Environment File: $install_dir/.env"
    echo "Claude Config: $claude_config"
    
    if [ -f "$install_dir/.env" ]; then
        echo ""
        echo "Environment Variables:"
        cat "$install_dir/.env" | sed 's/ZENDESK_API_KEY=.*/ZENDESK_API_KEY=***REDACTED***/'
    fi
    
    echo ""
    echo "Python Environment Info:"
    cd "$install_dir"
    uv run python --version 2>/dev/null || echo "Could not get Python version"
    
    echo ""
    echo "Installed Packages:"
    uv pip list 2>/dev/null | head -10 || echo "Could not list packages"
    
    echo ""
    echo "Certificate Bundle Location:"
    uv run python -c "import certifi; print(certifi.where())" 2>/dev/null || echo "Could not get certifi location"
    
    echo ""
    echo "System Info:"
    echo "macOS Version: $(sw_vers -productVersion)"
    echo "Architecture: $(uname -m)"
    echo "Homebrew Version: $(brew --version | head -1)"
    
    echo ""
    print_info "=== END DEBUGGING INFORMATION ==="
}

# Main installation function
main() {
    echo "========================================================="
    echo "   Zendesk MCP for Claude - macOS Enterprise Installer"
    echo "     (Self-contained version with embedded certificates)"
    echo "========================================================="
    echo ""

    # Check if running on macOS
    if [[ "$OSTYPE" != "darwin"* ]]; then
        print_error "This script is designed specifically for macOS systems."
        exit 1
    fi

    # Step 0.5: Check for existing installations FIRST
    print_step "Checking for existing Zendesk MCP installations..."

    local existing_servers
    existing_servers=$(detect_existing_installations)

    if [ -n "$existing_servers" ]; then
        # Found existing installation(s), handle them
        handle_existing_installations "$existing_servers"

        # If we're in update mode, credentials are already set
        # If we're in new instance mode, we'll ask for new credentials below
    else
        print_info "No existing installations found. Proceeding with fresh installation..."
        export UPDATE_MODE="false"
        export NEW_INSTANCE="false"
    fi

    # Step 0: Display prerequisites (skip if updating)
    if [ "$UPDATE_MODE" != "true" ]; then
        echo ""
        echo "Before running this script, please ensure you have:"
        echo "1. Generated a Zendesk API token at your Zendesk admin panel"
        echo "2. Claude Desktop app installed on your macOS system"
        echo "3. Administrator privileges (for some certificate operations)"
        echo ""
        echo "Note: This script contains all necessary certificates embedded within it."
        echo "No external files are required."
        echo ""
        read -p "Press Enter to continue..."
        echo ""
    fi

    # Step 1: Install Homebrew if needed
    print_step "Checking for Homebrew installation..."
    if command_exists brew; then
        print_success "Homebrew is already installed"
    else
        install_homebrew
    fi

    # Step 2: Get user inputs (skip if updating with existing credentials)
    if [ "$UPDATE_MODE" != "true" ]; then
        print_step "Collecting Zendesk configuration..."

        # Only prompt if not already set by update flow
        if [ -z "$ZENDESK_SUBDOMAIN" ]; then
            ZENDESK_SUBDOMAIN=""
            ZENDESK_EMAIL=""
            ZENDESK_API_KEY=""
            INSTALL_DIR=""

            get_input "Enter your Zendesk subdomain (e.g., 'pionex' for pionex.zendesk.com)" "pionex" ZENDESK_SUBDOMAIN
            get_input "Enter your Zendesk email" "" ZENDESK_EMAIL
            get_input "Enter your Zendesk API key" "" ZENDESK_API_KEY

            # For new instance, suggest a different directory
            if [ "$NEW_INSTANCE" == "true" ]; then
                DEFAULT_NEW_DIR="$HOME/zendesk-mcp-server-$ZENDESK_SUBDOMAIN"
                get_input "Enter installation directory" "$DEFAULT_NEW_DIR" INSTALL_DIR
            else
                get_input "Enter installation directory" "$DEFAULT_INSTALL_DIR" INSTALL_DIR
            fi
        fi

        echo ""
        print_step "Configuration summary:"
        echo "  Subdomain: $ZENDESK_SUBDOMAIN"
        echo "  Email: $ZENDESK_EMAIL"
        echo "  API Key: ${ZENDESK_API_KEY:0:10}..."
        echo "  Install Directory: $INSTALL_DIR"
        if [ "$NEW_INSTANCE" == "true" ]; then
            echo "  Mode: New instance (alongside existing)"
        fi
        echo ""
        read -p "Continue with installation? (y/N): " confirm
        if [[ ! $confirm =~ ^[Yy]$ ]]; then
            echo "Installation cancelled."
            exit 0
        fi
    else
        echo ""
        print_info "Update mode: Reusing existing credentials"
        print_info "  Subdomain: $ZENDESK_SUBDOMAIN"
        print_info "  Install Directory: $INSTALL_DIR"
        echo ""
    fi
    
    # Step 3: Install required tools via Homebrew
    print_step "Installing required tools..."
    
    # Install jq for JSON manipulation
    if command_exists jq; then
        print_success "jq is already installed"
    else
        print_step "Installing jq..."
        brew install jq
        print_success "jq installed successfully"
    fi
    
    # Install uv for Python package management
    if command_exists uv; then
        print_success "uv is already installed"
    else
        print_step "Installing uv..."
        brew install uv
        print_success "uv installed successfully"
    fi
    
    # Get the absolute path to uv
    UV_PATH=$(which uv)
    print_info "uv installed at: $UV_PATH"
    
    # Step 4: Download and extract source code from GitHub
    print_step "Downloading Zendesk MCP server source code..."
    
    # Create temporary directory
    TEMP_DIR=$(mktemp -d)
    cd "$TEMP_DIR"
    
    # Download the zip file from GitHub
    print_info "Downloading source code from GitHub..."
    GITHUB_URL="https://github.com/lyb0307/zendesk-mcp-server/archive/refs/heads/main.zip"
    
    if curl -L -o zendesk-mcp.zip "$GITHUB_URL"; then
        print_success "Source code downloaded successfully"
    else
        print_error "Failed to download source code from GitHub"
        exit 1
    fi
    
    # Extract the zip file
    print_step "Extracting source code..."
    if unzip -q zendesk-mcp.zip; then
        print_success "Source code extracted successfully"
        SOURCE_DIR="$TEMP_DIR/zendesk-mcp-server-main"
    else
        print_error "Failed to extract source code"
        exit 1
    fi
    
    # Step 5: Copy source to installation directory
    print_step "Installing to $INSTALL_DIR..."
    
    # Remove existing installation if present
    if [ -d "$INSTALL_DIR" ]; then
        print_warning "Existing installation found. Backing up..."
        mv "$INSTALL_DIR" "${INSTALL_DIR}.backup.$(date +%Y%m%d_%H%M%S)"
    fi
    
    # Create parent directory if needed
    mkdir -p "$(dirname "$INSTALL_DIR")"
    
    # Copy source code
    cp -r "$SOURCE_DIR" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    
    print_success "Source code installed to $INSTALL_DIR"
    
    # Step 6: Install enterprise certificates
    install_enterprise_certs "$INSTALL_DIR"
    
    # Step 7: Build the MCP server
    print_step "Building MCP server..."
    uv build
    print_success "MCP server built successfully"
    
    # Step 8: Create .env file
    print_step "Creating configuration file..."
    cat > .env << EOF
ZENDESK_SUBDOMAIN=$ZENDESK_SUBDOMAIN
ZENDESK_EMAIL=$ZENDESK_EMAIL
ZENDESK_API_KEY=$ZENDESK_API_KEY
EOF
    print_success "Configuration file created"
    
    # Step 9: Configure Claude Desktop
    print_step "Configuring Claude Desktop..."

    CLAUDE_CONFIG_FILE="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

    # Determine server name based on mode
    local SERVER_NAME
    if [ "$UPDATE_MODE" == "true" ]; then
        # Updating existing installation, use the same server name
        SERVER_NAME="$UPDATE_SERVER_NAME"
        print_info "Updating existing server: $SERVER_NAME"
    elif [ "$NEW_INSTANCE" == "true" ]; then
        # New instance alongside existing, use pattern: zendesk-{subdomain}
        SERVER_NAME="zendesk-$ZENDESK_SUBDOMAIN"
        print_info "Creating new server instance: $SERVER_NAME"
    else
        # Fresh installation, use default "zendesk" name
        SERVER_NAME="zendesk"
        print_info "Creating server: $SERVER_NAME"
    fi

    # Update Claude configuration with the determined server name
    update_claude_config "$CLAUDE_CONFIG_FILE" "$INSTALL_DIR" "$UV_PATH" "$SERVER_NAME"

    print_success "Claude Desktop configuration updated"
    print_success "Configuration file location: $CLAUDE_CONFIG_FILE"
    print_success "Server name: $SERVER_NAME"
    
    # Step 10: Run tests
    test_installation "$INSTALL_DIR" "$ZENDESK_SUBDOMAIN"
    
    # Step 11: Cleanup
    print_step "Cleaning up temporary files..."
    rm -rf "$TEMP_DIR"
    
    # Step 12: Final instructions
    echo ""
    echo "========================================================="
    if [ "$UPDATE_MODE" == "true" ]; then
        echo "               Update Complete!"
    else
        echo "               Installation Complete!"
    fi
    echo "========================================================="
    echo ""
    if [ "$UPDATE_MODE" == "true" ]; then
        print_success "Zendesk MCP server has been successfully updated!"
    else
        print_success "Zendesk MCP server has been successfully installed and configured!"
    fi
    echo ""
    echo "Next steps:"
    echo "1. 🔄 Quit and restart the Claude Desktop app"
    echo "2. ➕ You should see 'Add from $SERVER_NAME' when clicking the '+' button"
    echo "3. 🧪 Test with example prompts:"
    echo "   • 'Summarize all conversations of Zendesk ticket [TICKET_ID]'"
    echo "   • 'Draft a response for Zendesk ticket [TICKET_ID] using knowledge base articles'"
    echo ""
    echo "📁 Configuration details:"
    echo "   MCP Server name: $SERVER_NAME"
    echo "   Installation directory: $INSTALL_DIR"
    echo "   Claude config file: $CLAUDE_CONFIG_FILE"
    echo "   Zendesk subdomain: $ZENDESK_SUBDOMAIN"
    echo "   Enterprise certificates: ✅ Installed (embedded)"
    if [ "$UPDATE_MODE" == "true" ]; then
        echo "   Mode: Update (credentials preserved)"
    elif [ "$NEW_INSTANCE" == "true" ]; then
        echo "   Mode: New instance (alongside existing)"
    fi
    echo ""
    print_warning "🚨 IMPORTANT: Restart Claude Desktop app to activate the integration!"
    echo ""
    print_info "💡 This script is self-contained - all certificates are embedded"
    print_info "💡 If you encounter issues, check the debugging information above"
    print_info "💡 For support, provide the debugging information to your IT team"
    if [ "$UPDATE_MODE" == "true" ]; then
        print_info "💡 To update again, simply run this installer script"
    fi
    echo ""
}

# Run main function
main "$@"