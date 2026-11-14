# Make Strata Launcher yours

## Extra flags

On the **Setup** tab, **Extra flags** takes any other `strata launch` flags. They are added to every start. Example:

```
--set resident-experts=on
```

## Your own settings list

The settings the window shows are data, not code. The built-in list is
[`default_options.json`](../src/strata_launcher/default_options.json). To change it, press **Change the settings list…**
on the **App** tab. That opens your own `options.json` in your settings folder. Close the launcher and open it again
after you change the file.

In your file:

- `settings`: a list. An entry with the key of a built-in setting replaces it. A new key adds a setting.
- `remove`: keys of built-in settings to hide.
- `address`: the address the launcher asks for live figures. `{port}` is the value of the setting with the key `port`.

Example: add a switch, give the port another default, and hide two settings.

```json
{
  "settings": [
    {
      "key": "low_ram",
      "group": "Speed and memory",
      "label": "Low-RAM mode",
      "help": "Keeps in RAM only the parts of the model that no graphics card holds.",
      "type": "switch",
      "default": true,
      "on": ["--set", "resident-experts=on"],
      "off": []
    },
    {
      "key": "port",
      "group": "Access",
      "label": "Port",
      "type": "number",
      "default": 9000,
      "min": 1024,
      "max": 65535,
      "flags": ["--port", "{value}"]
    }
  ],
  "remove": ["speed", "tune"]
}
```

Setting types:

| Type | Fields | What it adds to the command |
| --- | --- | --- |
| `choice` | `options` (a list of `[value, text shown]`), `flags`, optional `flags_for`, optional `"editable": true` | `flags` with `{value}` replaced. `flags_for` gives other flags for particular values. |
| `number` | `min`, `max`, `step`, `flags`, optional `"optional": true` | `flags` with `{value}` replaced. With `optional`, "Strata's default" adds nothing. |
| `switch` | `on`, `off` | The `on` flags or the `off` flags. |
| `text` | `flags`, optional `placeholder` | `flags` with `{value}` replaced. Empty adds nothing. |

A broken entry does not stop the launcher. It is left out, and the Log tab says what is wrong with it.
