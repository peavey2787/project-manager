# Project Repo Manager v0.0.49

Project Repo Manager is a small desktop app for managing local development projects, project ZIPs, commands, files, prompts, notes, and related browser windows from one place.

It is designed for workflows where you regularly:

- download updated project ZIPs,
- extract or replace a local repository,
- run project commands and scripts,
- watch command status and copy useful error output,
- keep project-specific URLs, prompts, and notes,
- move files or ZIPs into ChatGPT,
- and keep several projects organized without constantly switching between Explorer, terminals, and browser windows.
<img width="1407" height="1110" alt="image" src="https://github.com/user-attachments/assets/2bd7bf80-1750-49f9-981c-62e4856fa8c9" />
The app is written in Python and uses the standard Tkinter desktop UI. It does not require third-party Python packages for normal use.

## Requirements

- Python 3.11 or newer
- Windows is recommended for the full browser, accessibility, clipboard, and terminal-management features

## Start the app

On Windows, double-click:

```text
run.cmd
```

Or run:

```text
python main.py
```

## Basic use

### 1. Add a project

Right-click the **Projects** list and choose **Add Project**.

Set the project folder, ZIP matching information, repository location, and any other project-specific options you want.

Each project keeps its own settings, URLs, commands, prompts, notes, snapshots, and selected prompt/note.

### 2. Use Main actions

The left-side **Main actions** area contains the common project operations, including:

- extract the newest matching ZIP,
- replace the local repo,
- ZIP the current project,
- open the project in Explorer,
- manage snapshots,
- open, focus, close, or work with the project's ChatGPT window,
- paste project ZIPs, output, errors, prompts, and notes into ChatGPT,
- focus or close project command windows,
- and close project URL windows.

Hover over an icon to see what it does.

### 3. Commands

Use the **Commands** tab to save commands or scripts you run often.

Commands can be run manually or automatically after a successful extraction. You can control their order, run commands with the same order together, choose whether to wait for an exit code or a delay, and optionally close a command window automatically after a successful run.

Project Repo Manager can also focus managed command windows and copy their full output or detected error output.

### 4. URLs and ChatGPT

Use the **URLs** tab to save project-related web pages.

Project Repo Manager can discover already-open browser windows, focus them, close them, and remember which browser window belongs to which saved URL.

ChatGPT URLs can also be monitored for states such as running, finished, or error.

### 5. Files

The **Files** tab gives you a simple project file/folder tree and a basic text editor.

You can also:

- serve a selected folder over HTTP or HTTPS,
- generate a local HTTPS certificate or use your own certificate,
- paste a selected file into ChatGPT,
- or ZIP a selected folder and paste that ZIP into ChatGPT.

### 6. Prompts and Notes

Prompts and notes are project-specific.

Each project starts with the standard default prompts, but you can edit or add your own. The selected prompt and selected note are remembered separately for every project.

## Browser accessibility and user control

Project Repo Manager uses the browser's normal Windows accessibility information to identify things such as browser windows, URLs, ChatGPT controls, and response state.

It does not use a hidden browser, bypass login or access controls, scrape private account data behind the user's back, or automatically send ChatGPT messages for you. When it pastes text or files into ChatGPT, the content is placed into the normal ChatGPT interface and **you remain in control of whether it is sent**.

The goal is simply to make normal web use more convenient: find the correct window, focus it, paste something you selected, or read status that is already exposed through the operating system's accessibility interface.

This design is intended to stay within ordinary user-driven interaction rather than bypassing a website's controls. Website terms can change, so no desktop utility can make a universal legal guarantee for every service, but Project Repo Manager does not try to circumvent a site's security, authentication, rate limits, or access restrictions.

## A few useful tips

- Right-click a project for project-specific shortcuts and window controls.
- Drag projects in the Projects list to reorder them.
- Use **Discover URL** if a browser window is open but was not matched automatically.
- Use the **Accessibility Items** window when troubleshooting browser controls; its search box makes large accessibility trees easier to inspect.
- Save a project snapshot when you want later ZIP extractions to be checked against the expected project structure.
- Use **Copy Error Output** when a command fails; it copies from the first detected error through the end of the captured command output.

## Safety

Repository replacement and extraction can overwrite files. Review your project paths and replacement settings before using destructive actions, and keep important work committed or backed up.
