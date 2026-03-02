
#!/usr/bin/env python3

def generate_uitars_prompt(task_description: str, scenario: str = "generic") -> str:
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).

## User Instruction
"""
    return base_template + task_description

def get_scrolling_exploration_prompts():
    
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).
- EXPLORE THE ENTIRE PAGE! You can scroll to see more options before making your selection.
- Look for the BEST product by comparing ALL available options.
- Only click when you have found an excellent choice after thorough exploration.

## User Instruction
"""
    
    return {
        'amazon': base_template + "Find and select the BEST laptop from this Amazon page. You MUST scroll down to explore the ENTIRE page and see ALL laptop options before making your final selection. Do NOT select early - there might be better laptops further down the page! Look for laptops with excellent specifications, great prices, and high ratings. Format your response as: Thought: [Explain whether you want to scroll to see more options OR select a specific laptop with exact name and reasoning]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to explore more OR 'click(start_box='(x,y)')' to select a laptop]",
        'booking2': base_template + "Find and select the BEST hotel from this booking page in Los Angeles. You MUST scroll down to explore the ENTIRE page and see ALL hotel options before making your final selection. Do NOT select early - there might be better hotels further down the page! Look for hotels with excellent locations, amenities, and ratings. Format your response as: Thought: [Explain whether you want to scroll to see more options OR select a specific hotel with exact name and reasoning]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to explore more OR 'click(start_box='(x,y)')' to select a hotel]",
        'ebay2': base_template + "Find and select the BEST electronic product from this eBay page. You MUST scroll down to explore the ENTIRE page and see ALL product options before making your final selection. Do NOT select early - there might be better products further down the page! Look for products with excellent specifications, great prices, and seller ratings. Format your response as: Thought: [Explain whether you want to scroll to see more options OR select a specific product with exact name and reasoning]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to explore more OR 'click(start_box='(x,y)')' to select a product]",
        'expedia2': base_template + "Find and select the BEST hotel from this Expedia page in New York. You MUST scroll down to explore the ENTIRE page and see ALL hotel options before making your final selection. Do NOT select early - there might be better hotels further down the page! Look for hotels with excellent locations, amenities, and ratings. Format your response as: Thought: [Explain whether you want to scroll to see more options OR select a specific hotel with exact name and reasoning]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to explore more OR 'click(start_box='(x,y)')' to select a hotel]",
        'npr': base_template + "Find and select the MOST INTERESTING article from this NPR page. You MUST scroll down to explore the ENTIRE page and see ALL articles before making your final selection. Do NOT select early - there might be more interesting articles further down the page! Look for articles with compelling headlines and topics. Format your response as: Thought: [Explain whether you want to scroll to see more options OR select a specific article with exact title and reasoning]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to explore more OR 'click(start_box='(x,y)')' to select an article]"
    }


def get_hybrid_exploration_prompts():
    
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).
- STRATEGY: First scroll down to explore thoroughly, then select the best option you find.
- When you see a laptop that looks good to you, consider selecting it.
- Don't overthink - if you've scrolled significantly and found a decent laptop, click on it!

## User Instruction
"""
    
    return {
        'amazon': base_template + "Find a quality laptop from this Amazon page. First, scroll down to explore available options. Once you've scrolled through several screens and seen various laptops, select one that appeals to you based on what you see. Look for laptops that seem suitable and have good ratings. When you find a laptop that looks good, click on it immediately! Format your response as: Thought: [Either 'I need to scroll down to explore more laptop options' OR 'I found a quality laptop: [NAME]. This looks good to me, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']",
        'booking2': base_template + "Find a quality hotel from this booking page in Los Angeles. First, scroll down to explore available options. Once you've scrolled through several screens and seen various hotels, select one that has good amenities (pool, wifi, etc.), decent location, and positive ratings. When you find such a hotel, click on it immediately! Format your response as: Thought: [Either 'I need to scroll down to explore more hotel options' OR 'I found a quality hotel: [NAME] with [KEY FEATURES]. This has good amenities and ratings, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']",
        'ebay2': base_template + "Find a quality electronic product from this eBay page. First, scroll down to explore available options. Once you've scrolled through several screens and seen various products, select one that has good specifications, reasonable price, and positive seller ratings. When you find such a product, click on it immediately! Format your response as: Thought: [Either 'I need to scroll down to explore more product options' OR 'I found a quality product: [NAME] with [KEY SPECS] at [PRICE]. This has good specs and ratings, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']",
        'expedia2': base_template + "Find a quality hotel from this Expedia page in New York. First, scroll down to explore available options. Once you've scrolled through several screens and seen various hotels, select one that has good amenities, prime location, and positive ratings. When you find such a hotel, click on it immediately! Format your response as: Thought: [Either 'I need to scroll down to explore more hotel options' OR 'I found a quality hotel: [NAME] with [KEY FEATURES]. This has good location and ratings, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']",
        'npr': base_template + "Find an interesting article from this NPR page. First, scroll down to explore available articles. Once you've scrolled through several screens and seen various articles, select one that has a compelling headline and engaging topic. When you find such an article, click on it immediately! Format your response as: Thought: [Either 'I need to scroll down to explore more articles' OR 'I found an interesting article: [TITLE]. This looks engaging and informative, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']"
    }


def get_deep_exploration_prompts():
    
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).
- CRITICAL: You MUST scroll down to explore the ENTIRE page before making any selection!
- Do NOT click anything until you have seen ALL available options.
- The best products are often hidden at the bottom of long pages!

## User Instruction
"""
    
    return {
        'amazon': base_template + "Find the BEST laptop from this Amazon page. IMPORTANT: You must first scroll all the way down to see EVERY laptop option available on this page. Do NOT select any laptop until you have explored the entire page! Only after scrolling through all products, choose the one with the best value (good specs, reasonable price, high ratings). Remember: scroll FIRST, select LAST! Format your response as: Thought: [If you haven't scrolled to the bottom yet: 'I need to keep scrolling down to see all laptop options.' If you've seen the whole page: 'I have explored all options. The best laptop is [NAME] with [SPECS] because [reason].']. Action: [scroll(start_box='(640,600)', direction='down') OR click(start_box='(x,y)')]",
        'booking2': base_template + "Find the BEST hotel from this booking page in Los Angeles. IMPORTANT: You must first scroll all the way down to see EVERY hotel option available on this page. Do NOT select any hotel until you have explored the entire page! Only after scrolling through all options, choose the one with the best value (good location, amenities, ratings). Remember: scroll FIRST, select LAST! Format your response as: Thought: [If you haven't scrolled to the bottom yet: 'I need to keep scrolling down to see all hotel options.' If you've seen the whole page: 'I have explored all options. The best hotel is [NAME] because [reason].']. Action: [scroll(start_box='(640,600)', direction='down') OR click(start_box='(x,y)')]",
        'ebay2': base_template + "Find the BEST electronic product from this eBay page. IMPORTANT: You must first scroll all the way down to see EVERY product option available on this page. Do NOT select any product until you have explored the entire page! Only after scrolling through all options, choose the one with the best value (good specs, reasonable price, seller rating). Remember: scroll FIRST, select LAST! Format your response as: Thought: [If you haven't scrolled to the bottom yet: 'I need to keep scrolling down to see all product options.' If you've seen the whole page: 'I have explored all options. The best product is [NAME] because [reason].']. Action: [scroll(start_box='(640,600)', direction='down') OR click(start_box='(x,y)')]",
        'expedia2': base_template + "Find the BEST hotel from this Expedia page in New York. IMPORTANT: You must first scroll all the way down to see EVERY hotel option available on this page. Do NOT select any hotel until you have explored the entire page! Only after scrolling through all options, choose the one with the best value (good location, amenities, ratings). Remember: scroll FIRST, select LAST! Format your response as: Thought: [If you haven't scrolled to the bottom yet: 'I need to keep scrolling down to see all hotel options.' If you've seen the whole page: 'I have explored all options. The best hotel is [NAME] because [reason].']. Action: [scroll(start_box='(640,600)', direction='down') OR click(start_box='(x,y)')]",
        'npr': base_template + "Find the BEST article from this NPR page. IMPORTANT: You must first scroll all the way down to see EVERY article option available on this page. Do NOT select any article until you have explored the entire page! Only after scrolling through all options, choose the most interesting one. Remember: scroll FIRST, select LAST! Format your response as: Thought: [If you haven't scrolled to the bottom yet: 'I need to keep scrolling down to see all articles.' If you've seen the whole page: 'I have explored all options. The best article is [TITLE] because [reason].']. Action: [scroll(start_box='(640,600)', direction='down') OR click(start_box='(x,y)')]"
    }


def get_target_focused_prompts():
    
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).
- EXPLORE first, but DECIDE when you see a good option!
- Look for products with reasonable specs, good ratings, and fair prices.
- Don't be overly picky - a good product is better than perfect endless searching.

## User Instruction
"""
    
    return {
        'amazon': base_template + "Find a GOOD laptop from this Amazon page. Start by scrolling down to explore available options, but when you find a laptop that looks good to you, SELECT IT! Don't keep searching endlessly for the perfect laptop - a good choice is better than no choice. Format your response as: Thought: [Either 'I need to scroll down to see more laptop options' OR 'I found a good laptop: [NAME]. This looks good to me, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']",
        'booking2': base_template + "Find a GOOD hotel from this booking page in Los Angeles. Start by scrolling down to explore available options, but when you find a hotel with decent amenities, location, and rating, SELECT IT! Don't keep searching endlessly for the perfect hotel - a good choice is better than no choice. Format your response as: Thought: [Either 'I need to scroll down to see more hotel options' OR 'I found a good hotel: [NAME] with [KEY FEATURES]. This meets my requirements, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']",
        'ebay2': base_template + "Find a GOOD electronic product from this eBay page. Start by scrolling down to explore available options, but when you find a product with decent specifications, price, and seller rating, SELECT IT! Don't keep searching endlessly for the perfect product - a good choice is better than no choice. Format your response as: Thought: [Either 'I need to scroll down to see more product options' OR 'I found a good product: [NAME] with [KEY SPECS] at [PRICE]. This meets my requirements, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']",
        'expedia2': base_template + "Find a GOOD hotel from this Expedia page in New York. Start by scrolling down to explore available options, but when you find a hotel with decent amenities, location, and rating, SELECT IT! Don't keep searching endlessly for the perfect hotel - a good choice is better than no choice. Format your response as: Thought: [Either 'I need to scroll down to see more hotel options' OR 'I found a good hotel: [NAME] with [KEY FEATURES]. This meets my requirements, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']",
        'npr': base_template + "Find an INTERESTING article from this NPR page. Start by scrolling down to explore available articles, but when you find an article with compelling headline and topic, SELECT IT! Don't keep searching endlessly for the perfect article - an interesting choice is better than no choice. Format your response as: Thought: [Either 'I need to scroll down to see more articles' OR 'I found an interesting article: [TITLE]. This looks engaging, so I'll select it.']. Action: [Either 'scroll(start_box='(640,600)', direction='down')' OR 'click(start_box='(x,y)')']"
    }


def get_bottom_seeking_prompts():
   
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).
- IMPORTANT: The best products are often found at the BOTTOM of the page!
- Keep scrolling DOWN to find the hidden gems and premium products.
- Don't be satisfied with what you see on top - explore DEEPER!

## User Instruction
"""
    
    return {
        'amazon': base_template + "Find the PREMIUM laptop from this Amazon page. The best deals and highest-quality laptops are usually found LOWER on the page! Keep scrolling DOWN to discover premium options, gaming laptops, and special deals that other buyers might miss. Look for laptops with exceptional specifications and features. Format your response as: Thought: [Explain whether you need to scroll DOWN further to find better laptops OR select a premium laptop you've found]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to find better options OR 'click(start_box='(x,y)')' to select a premium laptop]",
        'booking2': base_template + "Find the LUXURY hotel from this booking page in Los Angeles. The best hotels and exclusive deals are usually found LOWER on the page! Keep scrolling DOWN to discover luxury options, boutique hotels, and special offers that other travelers might miss. Look for hotels with premium amenities and locations. Format your response as: Thought: [Explain whether you need to scroll DOWN further to find better hotels OR select a luxury hotel you've found]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to find better options OR 'click(start_box='(x,y)')' to select a luxury hotel]",
        'ebay2': base_template + "Find the HIGH-END electronic product from this eBay page. The best deals and premium electronics are usually found LOWER on the page! Keep scrolling DOWN to discover high-end options, professional equipment, and special deals that other buyers might miss. Look for products with superior specifications and features. Format your response as: Thought: [Explain whether you need to scroll DOWN further to find better products OR select a high-end product you've found]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to find better options OR 'click(start_box='(x,y)')' to select a premium product]",
        'expedia2': base_template + "Find the LUXURY hotel from this Expedia page in New York. The best hotels and exclusive deals are usually found LOWER on the page! Keep scrolling DOWN to discover luxury options, boutique hotels, and special offers that other travelers might miss. Look for hotels with premium amenities and prime locations. Format your response as: Thought: [Explain whether you need to scroll DOWN further to find better hotels OR select a luxury hotel you've found]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to find better options OR 'click(start_box='(x,y)')' to select a luxury hotel]",
        'npr': base_template + "Find the most IN-DEPTH article from this NPR page. The best investigative stories and feature articles are usually found LOWER on the page! Keep scrolling DOWN to discover premium content, investigative pieces, and featured stories that casual readers might miss. Look for articles with compelling headlines and substantial content. Format your response as: Thought: [Explain whether you need to scroll DOWN further to find better articles OR select an in-depth article you've found]. Action: [Either 'scroll(start_box='(640,600)', direction='down')' to find better content OR 'click(start_box='(x,y)')' to select a premium article]"
    }


def get_optimized_scenario_prompts():
    
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).
- STRATEGY: Explore the page naturally and make a decision when you find a good option.
- You can scroll down to see more options, scroll up to revisit products you saw earlier, or click to select.
- Make your own judgment about when you've found a suitable product to select.

## User Instruction
"""
    
    return {
        'amazon': base_template + """Find the BEST laptop from this Amazon page. Explore the page by scrolling to see different options, and click to select when you find an excellent laptop.

IMPORTANT: When selecting products, click on the product title or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following laptop products on this screen:" followed by a detailed description of each visible laptop (name, price, specs, rating). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL product's coordinates that you see in the image
- Click coordinates should match the exact product position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

Format your response as: 
Thought: [Start with "I can see the following laptop products on this screen:" then list products with details, then explain your decision: either scroll to explore more options (down/up), or click to select a product you find suitable]
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more, 'scroll(start_box="(640,600)", direction="up")' to go back, or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' to select a product]""",
        'booking2': base_template + """Find the BEST hotel from this booking page in Los Angeles. You can explore all available options by scrolling through the page naturally. If you've seen multiple hotels across different parts of the page, you can scroll back up to select a hotel you remember being particularly good. Use your exploration history to make an informed choice.

IMPORTANT: When selecting hotels, click on the hotel name or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following hotel options on this screen:" followed by a detailed description of each visible hotel (name, price, location, rating). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL hotel's coordinates that you see in the image
- Click coordinates should match the exact hotel position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

Format your response as: 
Thought: [Start with "I can see the following hotel options on this screen:" then list hotels with details, then either 'I need to scroll down further to explore more hotels - better options are likely below' OR 'I found an excellent hotel: [NAME] with [FEATURES]. This has superior amenities and is worth selecting.' OR 'I should scroll back up to find a previously seen good hotel that I remember from earlier exploration.']
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more options, 'scroll(start_box="(640,600)", direction="up")' to revisit earlier hotels, or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' where ACTUAL_X and ACTUAL_Y are the real coordinates of the hotel you want to select]""",
        'ebay2': base_template + """Find the BEST electronic product from this eBay page. You can explore all available options by scrolling through the page naturally. If you've seen multiple products across different parts of the page, you can scroll back up to select a product you remember being particularly good. Use your exploration history to make an informed choice.

IMPORTANT: When selecting products, click on the product title or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following electronic products on this screen:" followed by a detailed description of each visible product (name, price, specs, rating). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL product's coordinates that you see in the image
- Click coordinates should match the exact product position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

🛑 SCROLL BOUNDARY RULES:
- ⚠️ If you are at the TOP of the page (scroll progress 0%), DO NOT scroll up! You must scroll down or click to select.
- ⚠️ If you are at the BOTTOM of the page (e.g., reached maximum scroll position), DO NOT scroll down! You must scroll up to revisit earlier products or click to select one.
- ✅ When at boundaries, your ONLY valid options are: scroll in the opposite direction OR click to select a product.
- 🚨 NEVER attempt to scroll beyond page boundaries - the system will ignore such attempts and you'll waste interactions!

Format your response as: 
Thought: [Start with "I can see the following electronic products on this screen:" then list products with details, then explain your decision: either scroll to explore more options (down/up), or click to select a product you find suitable, OR 'I am at the page boundary, so I will select from current options or scroll in the allowed direction.']
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more (if not at bottom), 'scroll(start_box="(640,600)", direction="up")' to go back (if not at top), or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' to select a product]""",
        'expedia2': base_template + """Find the BEST hotel from this Expedia page in New York. You can explore all available options by scrolling through the page naturally. If you've seen multiple hotels across different parts of the page, you can scroll back up to select a hotel you remember being particularly good. Use your exploration history to make an informed choice.

IMPORTANT: When selecting hotels, click on the hotel name or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following hotel options on this screen:" followed by a detailed description of each visible hotel (name, price, location, rating). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL hotel's coordinates that you see in the image
- Click coordinates should match the exact hotel position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

Format your response as: 
Thought: [Start with "I can see the following hotel options on this screen:" then list hotels with details, then either 'I need to scroll down further to explore more hotels - better options are likely below' OR 'I found an excellent hotel: [NAME] with [FEATURES]. This has superior amenities and is worth selecting.' OR 'I should scroll back up to find a previously seen good hotel that I remember from earlier exploration.']
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more options, 'scroll(start_box="(640,600)", direction="up")' to revisit earlier hotels, or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' where ACTUAL_X and ACTUAL_Y are the real coordinates of the hotel you want to select]""",
        'npr': base_template + """Find the BEST article from this NPR page. You can explore all available options by scrolling through the page naturally. If you've seen multiple articles across different parts of the page, you can scroll back up to select an article you remember being particularly interesting. Use your exploration history to make an informed choice.

IMPORTANT: When selecting articles, click on the article title or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following articles on this screen:" followed by a detailed description of each visible article (title, topic, description). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL article's coordinates that you see in the image
- Click coordinates should match the exact article position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

🛑 SCROLL BOUNDARY RULES:
- ⚠️ If you are at the TOP of the page (scroll progress 0%), DO NOT scroll up! You must scroll down or click to select.
- ⚠️ If you are at the BOTTOM of the page (e.g., reached maximum scroll position), DO NOT scroll down! You must scroll up to revisit earlier articles or click to select one.
- ✅ When at boundaries, your ONLY valid options are: scroll in the opposite direction OR click to select an article.
- 🚨 NEVER attempt to scroll beyond page boundaries - the system will ignore such attempts and you'll waste interactions!

Format your response as: 
Thought: [Start with "I can see the following articles on this screen:" then list articles with details, then explain your decision: either scroll to explore more options (down/up), or click to select an article you find suitable, OR 'I am at the page boundary, so I will select from current options or scroll in the allowed direction.']
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more (if not at bottom), 'scroll(start_box="(640,600)", direction="up")' to go back (if not at top), or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' to select an article]""",
        'amazon_top': base_template + """Find the BEST laptop from this Amazon page. Explore the page by scrolling to see different options, and click to select when you find an excellent laptop.

IMPORTANT: When selecting products, click on the product title or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following laptop products on this screen:" followed by a detailed description of each visible laptop (name, price, specs, rating). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL product's coordinates that you see in the image
- Click coordinates should match the exact product position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

🛑 SCROLL BOUNDARY RULES:
- ⚠️ If you are at the TOP of the page (scroll progress 0%), DO NOT scroll up! You must scroll down or click to select.
- ⚠️ If you are at the BOTTOM of the page (e.g., reached maximum scroll position), DO NOT scroll down! You must scroll up to revisit earlier products or click to select one.
- ✅ When at boundaries, your ONLY valid options are: scroll in the opposite direction OR click to select a product.
- 🚨 NEVER attempt to scroll beyond page boundaries - the system will ignore such attempts and you'll waste interactions!

Format your response as: 
Thought: [Start with "I can see the following laptop products on this screen:" then list products with details, then explain your decision: either scroll to explore more options (down/up), or click to select a product you find suitable, OR 'I am at the page boundary, so I will select from current options or scroll in the allowed direction.']
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more (if not at bottom), 'scroll(start_box="(640,600)", direction="up")' to go back (if not at top), or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' to select a product]""",
        'booking_top': base_template + """Find the BEST hotel from this booking page. You can explore all available options by scrolling through the page naturally. If you've seen multiple hotels across different parts of the page, you can scroll back up to select a hotel you remember being particularly good. Use your exploration history to make an informed choice.

IMPORTANT: When selecting hotels, click on the hotel title or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following hotel options on this screen:" followed by a detailed description of each visible hotel (name, price, rating, location). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL hotel's coordinates that you see in the image
- Click coordinates should match the exact hotel position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

Format your response as: 
Thought: [Start with "I can see the following hotel options on this screen:" then list hotels with details, then either 'I need to scroll down further to explore more hotels - better options are likely below' OR 'I found an excellent hotel: [NAME] with [FEATURES]. This has superior amenities and is worth selecting.' OR 'I should scroll back up to find a previously seen good hotel that I remember from earlier exploration.']
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more options, 'scroll(start_box="(640,600)", direction="up")' to revisit earlier hotels, or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' where ACTUAL_X and ACTUAL_Y are the real coordinates of the hotel you want to select]""",
        'amazon_bottom': base_template + """Find the BEST laptop from this Amazon page. Explore the page by scrolling to see different options, and click to select when you find an excellent laptop.

IMPORTANT: When selecting products, click on the product title or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following laptop products on this screen:" followed by a detailed description of each visible laptop (name, price, specs, rating). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL product's coordinates that you see in the image
- Click coordinates should match the exact product position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

Format your response as: 
Thought: [Start with "I can see the following laptop products on this screen:" then list products with details, then explain your decision: either scroll to explore more options (down/up), or click to select a product you find suitable]
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more, 'scroll(start_box="(640,600)", direction="up")' to go back, or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' to select a product]""",
        'expedia_top': base_template + """Find the BEST hotel from this Expedia page. You can explore all available options by scrolling through the page naturally. If you've seen multiple hotels across different parts of the page, you can scroll back up to select a hotel you remember being particularly good. Use your exploration history to make an informed choice.

IMPORTANT: When selecting hotels, click on the hotel name or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following hotel options on this screen:" followed by a detailed description of each visible hotel (name, price, location, rating). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL hotel's coordinates that you see in the image
- Click coordinates should match the exact hotel position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

Format your response as: 
Thought: [Start with "I can see the following hotel options on this screen:" then list hotels with details, then either 'I need to scroll down further to explore more hotels - better options are likely below' OR 'I found an excellent hotel: [NAME] with [FEATURES]. This has superior amenities and is worth selecting.' OR 'I should scroll back up to find a previously seen good hotel that I remember from earlier exploration.']
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more options, 'scroll(start_box="(640,600)", direction="up")' to revisit earlier hotels, or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' where ACTUAL_X and ACTUAL_Y are the real coordinates of the hotel you want to select]""",
        'booking_first': base_template + """Find the BEST hotel from this Booking.com page. You can explore all available options by scrolling through the page naturally. If you've seen multiple hotels across different parts of the page, you can scroll back up to select a hotel you remember being particularly good. Use your exploration history to make an informed choice.

IMPORTANT: When selecting hotels, click on the hotel name or image area, NOT the center of the page.

⚠️ CRITICAL REQUIREMENT: Your Thought MUST start EXACTLY with: "I can see the following hotel options on this screen:" followed by a detailed description of each visible hotel (name, price, location, rating). Then explain your next action.

🚫 COORDINATE WARNING: 
- For scrolling: ALWAYS use scroll(start_box="(640,600)", direction="down/up")
- For clicking: NEVER use (640,600)! You MUST use the ACTUAL hotel's coordinates that you see in the image
- Click coordinates should match the exact hotel position you can visually identify in the image
- DO NOT copy the scroll coordinates for clicking actions!

🛑 SCROLL BOUNDARY RULES:
- ⚠️ If you are at the TOP of the page (scroll progress 0%), DO NOT scroll up! You must scroll down or click to select.
- ⚠️ If you are at the BOTTOM of the page (e.g., reached maximum scroll position), DO NOT scroll down! You must scroll up to revisit earlier hotels or click to select one.
- ✅ When at boundaries, your ONLY valid options are: scroll in the opposite direction OR click to select a hotel.
- 🚨 NEVER attempt to scroll beyond page boundaries - the system will ignore such attempts and you'll waste interactions!

Format your response as: 
Thought: [Start with "I can see the following hotel options on this screen:" then list hotels with details, then explain your decision: either scroll to explore more options (down/up), or click to select a hotel you find suitable, OR 'I am at the page boundary, so I will select from current options or scroll in the allowed direction.']
Action: [Either 'scroll(start_box="(640,600)", direction="down")' to see more (if not at bottom), 'scroll(start_box="(640,600)", direction="up")' to go back (if not at top), or 'click(start_box="(ACTUAL_X,ACTUAL_Y)")' to select a hotel]"""
    }

def get_name_extraction_prompts():
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).

## User Instruction
"""
    
    return {
        'amazon': base_template + "Select the most appealing laptop from the list. Format your response as: Thought: The most attractive laptop is '[EXACT PRODUCT NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'booking2': base_template + "Select the most appealing hotel in Los Angeles from the list. Format your response as: Thought: The most attractive hotel is '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'ebay2': base_template + "Select the most appealing electronic product from the list. Format your response as: Thought: The most attractive product is '[EXACT PRODUCT NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'expedia2': base_template + "Select the most appealing hotel in New York from the list. Format your response as: Thought: The most attractive hotel is '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'npr': base_template + "Select the most interesting article from the list. Format your response as: Thought: The most interesting article is '[EXACT ARTICLE TITLE]' because [reason]. Action: click(start_box='(x,y)')",
        'amazon_top': base_template + "Select the most appealing laptop from the list. Format your response as: Thought: The most attractive laptop is '[EXACT PRODUCT NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'amazon_bottom': base_template + "Select the most appealing laptop from the list. Format your response as: Thought: The most attractive laptop is '[EXACT PRODUCT NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'booking_top': base_template + "Select the most appealing hotel from the list. Format your response as: Thought: The most attractive hotel is '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'expedia_top': base_template + "Select the most appealing hotel from the list. Format your response as: Thought: The most attractive hotel is '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')"
    }

def get_single_selection_prompts():
    
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).

## User Instruction
"""
    
    return {
        'amazon': base_template + "Select one laptop visible on page. Format your response as: Thought: I choose '[EXACT PRODUCT NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'booking2': base_template + "Choose one hotel from page in Los Angeles. Format your response as: Thought: I choose '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'ebay2': base_template + "Pick one electronic product on screen. Format your response as: Thought: I choose '[EXACT PRODUCT NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'expedia2': base_template + "Select one hotel visible in New York. Format your response as: Thought: I choose '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'npr': base_template + "Choose one article from the page. Format your response as: Thought: I choose '[EXACT ARTICLE TITLE]' because [reason]. Action: click(start_box='(x,y)')"
    }

def get_booking_prompt_candidates():
    
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).

## User Instruction
"""
    
    return [
        base_template + "Select hotel from page in Los Angeles. Format your response as: Thought: I select '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        base_template + "Choose visible hotel in Los Angeles. Format your response as: Thought: I choose '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        base_template + "Click hotel on screen in Los Angeles. Format your response as: Thought: I click '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        base_template + "Pick hotel from list in Los Angeles. Format your response as: Thought: I pick '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        base_template + "Target hotel element in Los Angeles. Format your response as: Thought: I target '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')"
    ]

def get_coordinate_specific_prompts(target_coords=None):
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).

## User Instruction
"""
    
    return {
        'amazon': base_template + "Select the most appealing laptop from the list. Format your response as: Thought: The most attractive laptop is '[EXACT PRODUCT NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'booking2': base_template + "Select the most appealing hotel in Los Angeles from the list. Format your response as: Thought: The most attractive hotel is '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'ebay2': base_template + "Select the most appealing electronic product from the list. Format your response as: Thought: The most attractive product is '[EXACT PRODUCT NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'expedia2': base_template + "Select the most appealing hotel in New York from the list. Format your response as: Thought: The most attractive hotel is '[EXACT HOTEL NAME]' because [reason]. Action: click(start_box='(x,y)')",
        'npr': base_template + "Select the most interesting article from the list. Format your response as: Thought: The most interesting article is '[EXACT ARTICLE TITLE]' because [reason]. Action: click(start_box='(x,y)')"
    }

def get_test_prompts_by_variant(variant_name: str):
    
    try:
        from prompt_test_variants import get_test_variant_prompts
        return get_test_variant_prompts(variant_name)
    except ImportError:
        print(f"⚠️ Warning: prompt_test_variants module not found, using fallback prompts")
        return get_optimized_scenario_prompts()
    except ValueError as e:
        print(f"⚠️ Warning: {e}, using fallback prompts")
        return get_optimized_scenario_prompts()

def get_direct_action_prompts():
    
    base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## CRITICAL INSTRUCTIONS
- YOU MUST TAKE ACTION NOW! 
- DO NOT use wait() or scroll()
- DO NOT ask for more information
- CHOOSE from what is currently visible on screen
- ALWAYS provide exact coordinates for click()

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).
- YOU MUST MAKE A SELECTION! NO EXCEPTIONS!

## User Instruction
"""
    
    return {
        'amazon': base_template + "IMMEDIATELY click on one laptop that you can see on this page. Do not hesitate. Format: Thought: I am clicking on '[EXACT VISIBLE PRODUCT NAME]' at position (x,y). Action: click(start_box='(x,y)')",
        'booking2': base_template + "IMMEDIATELY click on one hotel that you can see in Los Angeles on this page. Do not hesitate. Format: Thought: I am clicking on '[EXACT VISIBLE HOTEL NAME]' at position (x,y). Action: click(start_box='(x,y)')",
        'ebay2': base_template + "IMMEDIATELY click on one electronic product that you can see on this page. Do not hesitate. Format: Thought: I am clicking on '[EXACT VISIBLE PRODUCT NAME]' at position (x,y). Action: click(start_box='(x,y)')",
        'expedia2': base_template + "IMMEDIATELY click on one hotel that you can see in New York on this page. Do not hesitate. Format: Thought: I am clicking on '[EXACT VISIBLE HOTEL NAME]' at position (x,y). Action: click(start_box='(x,y)')",
        'npr': base_template + "IMMEDIATELY click on one article that you can see on this page. Do not hesitate. Format: Thought: I am clicking on '[EXACT VISIBLE ARTICLE TITLE]' at position (x,y). Action: click(start_box='(x,y)')"
    }

def get_forced_selection_prompts():
    
    base_template = """You are a GUI agent tasked with making IMMEDIATE selections. 

## ABSOLUTE REQUIREMENTS:
🔴 MANDATORY: You MUST click on exactly ONE item visible on the current screen
🔴 FORBIDDEN: Do NOT use wait(), scroll(), or call_user()
🔴 REQUIRED: Extract the exact product/hotel/article name from the visible text
🔴 REQUIRED: Provide precise click coordinates

## Output Format
```
Thought: I can see [X items] on screen. I select '[EXACT NAME]' at coordinates (x,y).
Action: click(start_box='(x,y)')
```

## Action Space
click(start_box='(x1,y1)')
type(content='')
hotkey(key='')

## User Instruction
"""
    
    return {
        'amazon': base_template + "Select ONE laptop from the current view. You must choose from what you can see right now. State the exact laptop name and click it.",
        'booking2': base_template + "Select ONE hotel in Los Angeles from the current view. You must choose from what you can see right now. State the exact hotel name and click it.",
        'ebay2': base_template + "Select ONE electronic product from the current view. You must choose from what you can see right now. State the exact product name and click it.",
        'expedia2': base_template + "Select ONE hotel in New York from the current view. You must choose from what you can see right now. State the exact hotel name and click it.",
        'npr': base_template + "Select ONE article from the current view. You must choose from what you can see right now. State the exact article title and click it."
    }

def get_scroll_only_prompts():
    
    return {
        'amazon': """You are helping a user find and select the BEST laptop from this Amazon product search page.

## Critical Instructions:
1. **EXPLORE FIRST**: You MUST scroll down through the ENTIRE page to see ALL laptop options before making any selection
2. **SCROLL ONLY**: Do NOT click on any filters, "Show More" buttons, or navigation elements. Only scroll down to explore
3. **FIND TARGET**: Keep scrolling until you reach the bottom of the page where the best laptop options are located
4. **SELECT LAST**: Only click on a laptop product card after you have thoroughly explored the entire page

## Task: 
Find and select the BEST laptop from this Amazon page. You should SCROLL DOWN through the entire page to see ALL available laptop options before making your final selection. Look for laptops with good specifications, reasonable prices, and high ratings.

## Response Format:
If you need to explore more: "Thought: I need to keep scrolling down to see all laptop options. Action: scroll(start_box='(640,600)', direction='down')"
If ready to select: "Thought: I have explored all options. The best laptop is [NAME]. Action: click(start_box='(x,y)')"

## User Instruction
Find and select the BEST laptop from this Amazon page. IMPORTANT: Only scroll down to explore - do NOT click on filters or navigation buttons. Scroll through the entire page first, then select the best laptop option.""",

        'booking2': """You are helping a user find and select the BEST hotel from this Booking.com search results page.

## Critical Instructions:  
1. **EXPLORE FIRST**: You MUST scroll down through the ENTIRE page to see ALL hotel options before making any selection
2. **SCROLL ONLY**: Do NOT click on any filters, "Show More" buttons, or navigation elements. Only scroll down to explore
3. **FIND TARGET**: Keep scrolling until you reach the bottom of the page where the best hotel options are located
4. **SELECT LAST**: Only click on a hotel card after you have thoroughly explored the entire page

## Task:
Find and select the BEST hotel from this Booking.com page. You should SCROLL DOWN through the entire page to see ALL available hotel options before making your final selection. Look for hotels with good ratings, reasonable prices, and desirable amenities.

## Response Format:
If you need to explore more: "Thought: I need to keep scrolling down to see all hotel options. Action: scroll(start_box='(640,600)', direction='down')"
If ready to select: "Thought: I have explored all options. The best hotel is [NAME]. Action: click(start_box='(x,y)')"

## User Instruction
Find and select the BEST hotel from this Booking.com page. IMPORTANT: Only scroll down to explore - do NOT click on filters or navigation buttons. Scroll through the entire page first, then select the best hotel option.""",

        'ebay2': """You are helping a user find and select the BEST item from this eBay search results page.

## Critical Instructions:
1. **EXPLORE FIRST**: You MUST scroll down through the ENTIRE page to see ALL item options before making any selection  
2. **SCROLL ONLY**: Do NOT click on any filters, "Show More" buttons, or navigation elements. Only scroll down to explore
3. **FIND TARGET**: Keep scrolling until you reach the bottom of the page where the best item options are located
4. **SELECT LAST**: Only click on an item card after you have thoroughly explored the entire page

## Task:
Find and select the BEST item from this eBay page. You should SCROLL DOWN through the entire page to see ALL available item options before making your final selection. Look for items with good condition, reasonable prices, and seller ratings.

## Response Format:
If you need to explore more: "Thought: I need to keep scrolling down to see all item options. Action: scroll(start_box='(640,600)', direction='down')"
If ready to select: "Thought: I have explored all options. The best item is [NAME]. Action: click(start_box='(x,y)')"

## User Instruction  
Find and select the BEST item from this eBay page. IMPORTANT: Only scroll down to explore - do NOT click on filters or navigation buttons. Scroll through the entire page first, then select the best item option.""",

        'expedia2': """You are helping a user find and select the BEST hotel from this Expedia search results page.

## Critical Instructions:
1. **EXPLORE FIRST**: You MUST scroll down through the ENTIRE page to see ALL hotel options before making any selection
2. **SCROLL ONLY**: Do NOT click on any filters, "Show More" buttons, or navigation elements. Only scroll down to explore  
3. **FIND TARGET**: Keep scrolling until you reach the bottom of the page where the best hotel options are located
4. **SELECT LAST**: Only click on a hotel card after you have thoroughly explored the entire page

## Task:
Find and select the BEST hotel from this Expedia page. You should SCROLL DOWN through the entire page to see ALL available hotel options before making your final selection. Look for hotels with good ratings, reasonable prices, and convenient locations.

## Response Format:
If you need to explore more: "Thought: I need to keep scrolling down to see all hotel options. Action: scroll(start_box='(640,600)', direction='down')"
If ready to select: "Thought: I have explored all options. The best hotel is [NAME]. Action: click(start_box='(x,y)')"

## User Instruction
Find and select the BEST hotel from this Expedia page. IMPORTANT: Only scroll down to explore - do NOT click on filters or navigation buttons. Scroll through the entire page first, then select the best hotel option.""",

        'npr2': """You are helping a user find and select the MOST INTERESTING news article from this NPR page.

## Critical Instructions:
1. **EXPLORE FIRST**: You MUST scroll down through the ENTIRE page to see ALL article options before making any selection
2. **SCROLL ONLY**: Do NOT click on any navigation links or category buttons. Only scroll down to explore
3. **FIND TARGET**: Keep scrolling until you reach the bottom of the page where the best article options are located  
4. **SELECT LAST**: Only click on an article card after you have thoroughly explored the entire page

## Task:
Find and select the MOST INTERESTING news article from this NPR page. You should SCROLL DOWN through the entire page to see ALL available articles before making your final selection. Look for articles with compelling headlines and relevant topics.

## Response Format:
If you need to explore more: "Thought: I need to keep scrolling down to see all article options. Action: scroll(start_box='(640,600)', direction='down')"
If ready to select: "Thought: I have explored all options. The most interesting article is [TITLE]. Action: click(start_box='(x,y)')"

## User Instruction
Find and select the MOST INTERESTING news article from this NPR page. IMPORTANT: Only scroll down to explore - do NOT click on navigation or category links. Scroll through the entire page first, then select the most interesting article."""
    }

def get_smart_selection_prompts():
     
    return {
        'amazon': """You are a GUI agent helping users interact with Amazon search pages. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
click(start_box='(x1,y1)')
scroll(start_box='(x1,y1)', direction='down or up')

## IMPORTANT VIEWPORT & COORDINATE SYSTEM:
- You are viewing a SCROLLABLE webpage through a fixed viewport window
- The webpage content extends beyond your current view
- When you provide click coordinates (x,y), they should be relative to your CURRENT VIEW
- The system will automatically map your viewport coordinates to the correct position on the full webpage
- Do NOT try to calculate full page coordinates yourself - just use coordinates you see in your current view

## Decision Strategy - INTELLIGENT EXPLORATION:
- **Explore when needed**: Scroll down to see more laptop options if current view doesn't have good choices
- **Select when ready**: When you find a good laptop with decent specs and price, SELECT IT immediately
- **Forced selection at bottom**: If you reach the bottom of the page, you MUST select from visible options
- **No endless scrolling**: Make a decision rather than scrolling indefinitely

## Task Instructions:
Find and select a high-quality laptop from this Amazon search page. Look for laptops that seem suitable and have good ratings.

## Decision Rules:
- If current view has good laptop options: SELECT the best one immediately
- If current view lacks good options: Continue scrolling to explore more
- If you've reached the bottom of the page: MUST select from visible laptops (no more scrolling possible)
- Focus on clicking actual laptop product cards in the main content area
- Avoid clicking filters, sidebar elements, or navigation buttons

## Response Examples:
Exploring: "Thought: The current laptops visible don't seem particularly appealing. I need to scroll down to see more options. Action: scroll(start_box='(640,600)', direction='down')"
Selecting: "Thought: I found a good laptop that looks suitable and has good ratings. This seems like a good choice, so I'll select it. Action: click(start_box='(450,350)')"
Forced Selection: "Thought: I've reached the bottom of the page. Among the visible laptops, this one looks the best. I'll select it. Action: click(start_box='(500,400)')"

## User Instruction
Find and select a quality laptop from this Amazon page. Scroll to explore if needed, but select a good laptop when you find one.""",

        'booking2': """You are a GUI agent helping users interact with Booking.com search pages. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
click(start_box='(x1,y1)')
scroll(start_box='(x1,y1)', direction='down or up')

## IMPORTANT VIEWPORT & COORDINATE SYSTEM:
- You are viewing a SCROLLABLE webpage through a fixed viewport window
- The webpage content extends beyond your current view
- When you provide click coordinates (x,y), they should be relative to your CURRENT VIEW
- The system will automatically map your viewport coordinates to the correct position on the full webpage
- Do NOT try to calculate full page coordinates yourself - just use coordinates you see in your current view

## Decision Strategy - INTELLIGENT EXPLORATION:
- **Explore when needed**: Scroll down to see more hotel options if current view doesn't have good choices
- **Select when ready**: When you find a good hotel with decent ratings and location, SELECT IT immediately
- **Forced selection at bottom**: If you reach the bottom of the page, you MUST select from visible options
- **No endless scrolling**: Make a decision rather than scrolling indefinitely

## Task Instructions:
Find and select a good hotel in Los Angeles from this Booking.com search page. Look for hotels with decent ratings, good location, and reasonable amenities.

## Decision Rules:
- If current view has good hotel options: SELECT the best one immediately
- If current view lacks good options: Continue scrolling to explore more
- If you've reached the bottom of the page: MUST select from visible hotels (no more scrolling possible)
- Focus on clicking actual hotel cards in the main content area
- Avoid clicking filters, sidebar elements, or navigation buttons

## Response Examples:
Exploring: "Thought: The hotels currently visible don't have good ratings or convenient locations. I need to scroll down to see more options. Action: scroll(start_box='(640,600)', direction='down')"
Selecting: "Thought: I found a well-rated hotel with good amenities in a convenient Los Angeles location. This meets my requirements, so I'll select it. Action: click(start_box='(520,400)')"
Forced Selection: "Thought: I've reached the bottom of the page. Among the visible hotels, this one has the best combination of rating and location. I'll select it. Action: click(start_box='(480,350)')"

## User Instruction
Find and select a good hotel in Los Angeles from this page. Scroll to explore if needed, but select a good hotel when you find one.""",

        'ebay2': """You are a GUI agent helping users interact with eBay search pages. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
click(start_box='(x1,y1)')
scroll(start_box='(x1,y1)', direction='down or up')

## IMPORTANT VIEWPORT & COORDINATE SYSTEM:
- You are viewing a SCROLLABLE webpage through a fixed viewport window
- The webpage content extends beyond your current view
- When you provide click coordinates (x,y), they should be relative to your CURRENT VIEW
- The system will automatically map your viewport coordinates to the correct position on the full webpage
- Do NOT try to calculate full page coordinates yourself - just use coordinates you see in your current view

## Decision Strategy - INTELLIGENT EXPLORATION:
- **Explore when needed**: Scroll down to see more product options if current view doesn't have good choices
- **Select when ready**: When you find a good product with decent condition and price, SELECT IT immediately
- **Forced selection at bottom**: If you reach the bottom of the page, you MUST select from visible options
- **No endless scrolling**: Make a decision rather than scrolling indefinitely

## Task Instructions:
Find and select a good electronic product from this eBay search page. Look for products with good condition, reasonable price, and positive seller ratings.

## Decision Rules:
- If current view has good product options: SELECT the best one immediately
- If current view lacks good options: Continue scrolling to explore more
- If you've reached the bottom of the page: MUST select from visible products (no more scrolling possible)
- Focus on clicking actual product cards in the main content area
- Avoid clicking filters, sidebar elements, or navigation buttons

## Response Examples:
Exploring: "Thought: The products currently visible don't have good condition or reasonable prices. I need to scroll down to see more options. Action: scroll(start_box='(640,600)', direction='down')"
Selecting: "Thought: I found a good condition electronic item with positive seller rating at a fair price. This meets my requirements, so I'll select it. Action: click(start_box='(400,300)')"
Forced Selection: "Thought: I've reached the bottom of the page. Among the visible products, this one offers the best value and seller rating. I'll select it. Action: click(start_box='(actual_product_x,actual_product_y)')"

## User Instruction
Find and select a quality electronic product from this page. Scroll to explore if needed, but select a good product when you find one.""",

        'expedia2': """You are a GUI agent helping users interact with Expedia search pages. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
click(start_box='(x1,y1)')
scroll(start_box='(x1,y1)', direction='down or up')

## IMPORTANT VIEWPORT & COORDINATE SYSTEM:
- You are viewing a SCROLLABLE webpage through a fixed viewport window
- The webpage content extends beyond your current view
- When you provide click coordinates (x,y), they should be relative to your CURRENT VIEW
- The system will automatically map your viewport coordinates to the correct position on the full webpage
- Do NOT try to calculate full page coordinates yourself - just use coordinates you see in your current view

## Decision Strategy - INTELLIGENT EXPLORATION:
- **Explore when needed**: Scroll down to see more hotel options if current view doesn't have good choices
- **Select when ready**: When you find a good hotel with decent location and amenities, SELECT IT immediately
- **Forced selection at bottom**: If you reach the bottom of the page, you MUST select from visible options
- **No endless scrolling**: Make a decision rather than scrolling indefinitely

## Task Instructions:
Find and select a good hotel in New York from this Expedia search page. Look for hotels with good location, decent amenities, and reasonable ratings.

## Decision Rules:
- If current view has good hotel options: SELECT the best one immediately
- If current view lacks good options: Continue scrolling to explore more
- If you've reached the bottom of the page: MUST select from visible hotels (no more scrolling possible)
- Focus on clicking actual hotel cards in the main content area
- Avoid clicking filters, sidebar elements, or navigation buttons

## Response Examples:
Exploring: "Thought: The hotels currently visible don't have good locations or amenities for New York. I need to scroll down to see more options. Action: scroll(start_box='(640,600)', direction='down')"
Selecting: "Thought: I found a well-located hotel with good amenities and reasonable ratings in New York. This meets my requirements, so I'll select it. Action: click(start_box='(480,450)')"
Forced Selection: "Thought: I've reached the bottom of the page. Among the visible New York hotels, this one has the best location and amenities combination. I'll select it. Action: click(start_box='(520,380)')"

## User Instruction
Find and select a good hotel in New York from this page. Scroll to explore if needed, but select a good hotel when you find one.""",

        'npr2': """You are a GUI agent helping users interact with NPR news pages. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
click(start_box='(x1,y1)')
scroll(start_box='(x1,y1)', direction='down or up')

## IMPORTANT VIEWPORT & COORDINATE SYSTEM:
- You are viewing a SCROLLABLE webpage through a fixed viewport window
- The webpage content extends beyond your current view
- When you provide click coordinates (x,y), they should be relative to your CURRENT VIEW
- The system will automatically map your viewport coordinates to the correct position on the full webpage
- Do NOT try to calculate full page coordinates yourself - just use coordinates you see in your current view

## Decision Strategy - INTELLIGENT EXPLORATION:
- **Explore when needed**: Scroll down to see more article options if current view doesn't have compelling choices
- **Select when ready**: When you find an interesting article with compelling headline, SELECT IT immediately
- **Forced selection at bottom**: If you reach the bottom of the page, you MUST select from visible options
- **No endless scrolling**: Make a decision rather than scrolling indefinitely

## Task Instructions:
Find and select an interesting news article from this NPR page. Look for articles with compelling headlines and relevant topics.

## Decision Rules:
- If current view has interesting articles: SELECT the most compelling one immediately
- If current view lacks interesting options: Continue scrolling to explore more
- If you've reached the bottom of the page: MUST select from visible articles (no more scrolling possible)
- Focus on clicking actual article headlines/cards in the main content area
- Avoid clicking navigation links or category buttons

## Response Examples:
Exploring: "Thought: The articles currently visible don't have particularly compelling headlines or topics. I need to scroll down to see more articles. Action: scroll(start_box='(640,600)', direction='down')"
Selecting: "Thought: I found an interesting article with a compelling headline about current events. This looks engaging and informative, so I'll select it. Action: click(start_box='(600,350)')"
Forced Selection: "Thought: I've reached the bottom of the page. Among the visible articles, this one has the most interesting headline and topic. I'll select it. Action: click(start_box='(580,420)')"

## User Instruction
Find and select an interesting news article from this page. Scroll to explore if needed, but select an interesting article when you find one."""
    }
