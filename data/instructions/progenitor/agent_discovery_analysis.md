---
title: Agent discovery analysis
description: Asks the model to describe each discovered agent folder.
---
Analyze these agent folders and extract structured information for each valid agent:

{folder_descriptions}

Return a JSON object with an "agents" array containing information for each folder analyzed.
For folders that are valid agents, include full details.
For folders that are not valid agents, set is_valid_agent=false with a reason.
