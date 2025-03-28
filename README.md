
# Derafu: Django Fabric Deployer – Multi-Site Deployment Made Simple

![GitHub last commit](https://img.shields.io/github/last-commit/derafu/python-fabric-deployer/main)
![GitHub code size in bytes](https://img.shields.io/github/languages/code-size/derafu/python-fabric-deployer)
![GitHub issues](https://img.shields.io/github/issues-raw/derafu/python-fabric-deployer)


Derafu Deployer is a lightweight deployment system for managing multiple Django sites using Fabric2 . It supports deployment via local, SSH, or Docker runners, and provides key features like automatic backups, shared files/directories management, and per-site configuration.

---

## Features

- **Multi-Runner Support**: Automatically runs tasks locally, via SSH, or inside Docker containers.
- **Logging**: All deployment logs are streamed to console.
- **Backups**: Before each deploy, a `.tar.gz` backup is created and stored with support for limiting how many are kept.
- **Shared Files & Dirs**: Automatically symlinks files and folders like `.env`, `media/`, etc.
- **Writable Dirs**: Configure folders that require specific permissions (e.g., 775).
- **Dynamic Site Detection**: Uses `sites.yml` to configure and deploy any number of Django projects.
- **Versioned Releases**: Each deploy is stored in `releases/YYYYMMDD_HHMMSS_mmm`, keeping history clean and organized.
- **Symlink-Based Switching**: The `current` symlink always points to the latest working release, enabling atomic deployments.
- **Rollback on Failure**: If a deployment fails, the previous version is automatically restored.
- **Manual Rollback**: You can manually trigger a rollback with `fab2 rollback --site=...`.
- **Old Releases Cleanup**: Keeps only the last `N` releases (default: 5) to avoid clutter.
- **Release Locking**: Prevents concurrent deployments using a `.deploy.lock` file.
- **Manual Unlock**: Use `fab2 unlock --site=...` to remove a lock after an interrupted deployment.

---

## Requirements

- Python 3.13+
- Fabric2 3.2
- SSH access to your servers.
- Docker (if using Docker runner)
- Git repositories for your projects.

---

## Installation

```bash
pip install -r requirements.txt
```

**Note:** The tool is designed to be used standalone, not inside other project.

---

## Project Structure

```
.
├── LICENSE                       # License file
├── README.md                     # Project documentation
├── __init__.py                   # Required for Python package recognition
├── fabfile.py                    # Entry point for Fabric tasks (e.g., deploy)
├── requirements.txt              # Python dependencies (e.g., fabric2)
├── siteadd.py                    # Script to add new site entries to `sites.yml`
├── fabricator/                   # Core deployment logic and utilities
│   ├── __init__.py               # Makes fabricator a package
│   ├── deploy.py                 # Main deploy process controller
│   ├── logger.py                 # Logging utilities (console only)
│   ├── recipes.py                # Reusable deployment tasks (e.g. backup, shared dirs)
│   ├── runners.py                # Runner abstraction (local, SSH, Docker)
│   ├── sites.yml                 # Main configuration file with all project deployments
│   ├── sites-dist.yml            # Example configuration file (template)
│   └── utils.py                  # Utility functions

```

---

## Configuration

The main configuration is stored in `fabricator/sites.yml`, based on `sites-dist.yml`. Each key represents a site.

---

## Sites Configuration

Edit the sites.yml file located in the fabricator/ directory to configure your websites.

Each key represents a domain name (e.g., www.example.com). The value can be:

- A dictionary with detailed configuration options.

Example:

```yaml
www.example.com:
  repository: git@github.com:example/example.git
  branch: master
  deploy_path: /var/www/sites/www.example.com
  venv: .venv
  runner: docker
  docker_container: my-container
  docker_user: admin
  backup_path: /var/www/sites/backuplocal
  max_backups: 5
  shared_files:
    - .env
  shared_dirs:
    - media
  writable_dirs:
    - media
```
For simple configurations you can use:

```shell
./siteadd.py www.example.com git@github.com:example/example.git
```
---

## Available Configuration Options

| Key              | Required | Default         | Description                                                |
|------------------|----------|-----------------|------------------------------------------------------------|
| `repository`     | Yes      | —               | Git repository to clone                                    |
| `branch`         | No       | `master`        | Branch to deploy                                           |
| `deploy_path`    | Yes      | —               | Path where the site will be deployed                       |
| `venv`           | No       | `.venv`         | Virtualenv directory name                                  |
| `runner`         | No       | `local`         | Can be `local`, `docker`, or `ssh`                         |
| `docker_container` | Cond. | —               | Required if runner is `docker`                             |
| `docker_user`    | No       | —               | User to run Docker commands as                             |
| `backup_path`    | Yes      | —               | Where to store backups (must be defined)                   |
| `max_backups`    | No       | `5`             | How many backups to keep                                   |
| `shared_files`   | No       | `[]`            | List of files to symlink after each deploy                 |
| `shared_dirs`    | No       | `[]`            | List of directories to symlink after each deploy           |
| `writable_dirs`  | No       | `[]`            | Directories to make writable (chmod 775, etc.)             |

---


## Usage

### List Available Sites

To see all configured sites:

```bash
fab2 sites
```

---

### Deploy a Single Site (Local)

Default runner is `local`, so this will deploy locally:

```bash
fab2 deploy --site=www.example.com
```

---

### Deploy a Single Site (Docker)

Specify the site which has `"runner: docker"` in `sites.yml`:

```bash
fab2 deploy --site=www.example.com
```

You can also define the Docker container and user in the site config:

```yaml
www.example.com:
  runner: docker
  docker_container: my-container
  docker_user: admin
```

---

### Deploy a Single Site (Remote via SSH)

When `"runner: ssh"` is configured in `sites.yml`, the deploy will be done over SSH:

```yaml
www.example.com:
  runner: ssh
  host: 192.168.1.99
  user: deployer
```

You can override the SSH connection with environment variables:

```bash
DEPLOYER_HOST=192.168.1.99 fab2 deploy --site=www.example.com
```

Or also set user and port:

```bash
DEPLOYER_HOST=192.168.1.99 DEPLOYER_USER=admin DEPLOYER_PORT=22 fab2 deploy --site=www.example.com
```

---

### Deploy All Sites

Deploy all sites defined in `sites.yml`, one by one:

```bash
fab2 deploy_all
```

You can also deploy all sites remotely by using environment variables:

```bash
DEPLOYER_HOST=192.168.1.99 DEPLOYER_USER=admin fab2 deploy_all
```

Or limit to a specific runner (e.g., local only):

```bash
fab2 deploy_all --runner=local
```

### Rollback (Automatic and Manual)

#### Automatic Rollback on Failure

If a deployment fails during a critical step (like migrations), the system will **automatically rollback** to the previous release. This prevents broken sites from going live.

#### Manual Rollback

You can manually revert to the last successful release at any time using:

```bash
fab2 rollback --site=www.example.com
```

This will:

- Restore the last valid release from `releases/`
- Update the `current` symlink
- Restart Gunicorn or the appropriate service

### Rollback All Sites

```bash
fab2 rollback_all
```

### Unlock a Stuck Deployment

If a deployment was interrupted and the `.deploy.lock` file wasn't removed:

```bash
fab2 unlock --site=www.example.com
```

This forcibly removes the lock file and allows future deploys.

### Unlock All Sites

Force unlock for all sites at once (use with caution):

```bash
fab2 unlock_all
```

---


## Deployment Process

For each site, the deployment process performs the following steps:

1. **Acquire Lock**: Prevents concurrent deployments by creating a `.deploy.lock` file. If the site is already locked, the deploy is aborted.
2. **Check Remote**: Verifies SSH connection and deployment path.
3. **Create Backup**: Compresses current state before deploying.
4. **Update Code**: Clones Git repository into a temporary folder.
5. **Versioned Release Folder**: Moves code into `releases/YYYYMMDD_HHMMSS_mmm/` and updates the `current` symlink.
6. **Cleanup Old Releases**: Keeps only the latest `N` releases (configurable).
7. **Shared Files/Dirs**: Links shared files and directories (e.g., `.env`, `media/`).
8. **Writable Dirs**: Applies proper permissions to specified folders.
9. **Install Deps**: Installs Python dependencies in a virtualenv.
10. **Validate Migrations**: Runs `python manage.py migrate --plan` to ensure migrations won't fail.
11. **Migrate**: Applies Django database migrations.
12. **Collect Static**: Gathers static assets with `collectstatic`.
13. **Restart Services**: Reloads services like Gunicorn via socket or process restart.
14. **Rollback on Failure**: If a critical step fails, the system rolls back to the previous working release.
15. **Release Lock**: Always removes the `.deploy.lock` file at the end of the process.
16. **Logging**: Logs are streamed to console.

---

## Customization

You can customize the following behaviors in `recipes.py`:

- How backups are made.
- How services are restarted.
- How static files and dependencies are handled.

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## License

This package is open-sourced software licensed under the [MIT license](https://opensource.org/licenses/MIT).
