# pi_pico_es200
poll es200 batteries via serial

To use this code:

1. Create a file named `WIFI_CONFIG.py` on your MicroPython device, which contains two variables: `SSID` and `PASSWORD`:

    ```python
    SSID = "my wifi hotspot name"
    PASSWORD = "wifi password"
    ```

1. Add this to your main program code:

    ```python
    from ota import OTAUpdater
    from WIFI_CONFIG import SSID, PASSWORD

    firmware_url = "https://raw.githubusercontent.com/<username>/<repo_name>/<branch_name>"

    ```

    where `<username>` is your github username, `<repo_name>` is the name of the repository to check for updates and `<branch_name>` is the name of the branch to monitor.

1. Add this to your main program code:

    ```python
    ota_updater = OTAUpdater(SSID, PASSWORD, firmware_url, "test.py")
    ota_updater.download_and_install_update_if_available()

    ```
1. On your GitHub repository, add a `version.json` file, and add a `version` element to the JSON file, with a version number:

    ```json
    {
      "version":3
    }
    ```