from datetime import datetime
import math
from PIL import Image, ImageDraw, ImageFont
import os
import sys
import random

class DashboardTemplate():

    def __init__(self, lightning_time=5, hot_found=20):
        #should be integers
        self.lightning_time = lightning_time
        self.hot_found = hot_found


    def get_base_path(self):
        if getattr(sys, 'frozen', False):
            return sys._MEIPASS
        return os.path.dirname(os.path.abspath(__file__))

    def buildImage(self, title_name, median_response_time, percent_found, total_referrals,
                   percent_contacted, percent_successful_contact, percent_attended_church, uncontacted_people_list, output_path):
        # ======================
        # CONFIG
        # ======================
        IMAGE_PATH = os.path.join(self.get_base_path(), "images", "dashboard.png")

        LIGHTNING_ICON_PATH = os.path.join(self.get_base_path(), "images", "lightning.png")
        PEPPER_ICON_PATH = os.path.join(self.get_base_path(), "images", "hotpepper.png")

        BIG_FONT_PATH = os.path.join(self.get_base_path(), "fonts", "CalSans-Regular.ttf")
        SMALL_FONT_PATH = os.path.join(self.get_base_path(), "fonts", "CalSans-Regular.ttf")

        BIG_FONT_SIZE = 80
        SMALL_FONT_SIZE = 30
        LABEL_FONT_SIZE = 26

        FONT_TYPE2 = 30
        FONT_SMALL_TYPE2 = 15

        FONT_TYPE3 = 45
        FONT_SMALL_TYPE3 = 20

        BOTTOM_LIST_FONT_SIZE = 22

        TEXT_COLOR = (255, 255, 255)

        # ======================
        # ICON CONTROLS
        # ======================
        LIGHTNING_HEIGHT = 90
        LIGHTNING_OFFSET_X = -80
        LIGHTNING_OFFSET_Y = 90
        LIGHTNING_ABSOLUTE_POS = None

        PEPPER_HEIGHT = 110
        PEPPER_OFFSET_X = -80
        PEPPER_OFFSET_Y = 90
        PEPPER_ABSOLUTE_POS = None

        format_date_pattern = '%m/%d/%Y %I:%M:%S %p'

        # ======================
        # 🔥 ALL DYNAMIC TEXT DATA
        # ======================

        def safe_round(val, digits=2):
            return round(val, digits) if val is not None else 0

        TITLE_DATA = {
            "top": title_name,
            "bottom": "referral dashboard"
        }

        GAUGE_DATA = {
            "time": {
                "value": None if median_response_time is None else safe_round(median_response_time, 1),
                "unit": " min",
                "label": "Median response time"
            },
            "found": {
                "value": safe_round(percent_found, 1),
                "unit": " %",
                "label": "Found"
            }
        }

        MIDDLE_STATS = [
            {"center": (760, 205), "top": str(total_referrals), "bottom": "total referrals", "big": True},
            {"center": (760, 347), "top": f"{percent_contacted}%", "bottom": "attempted"},
            {"center": (760, 413), "top": f"{percent_successful_contact}%", "bottom": "successful contact"},
            {"center": (760, 543), "top": f"{percent_attended_church}%", "bottom": "at church"},
        ]

        PEOPLE = []
        uncontacted_people_list = uncontacted_people_list or []
        for p in uncontacted_people_list:
            PEOPLE.append({"name": p['name'], "date": datetime.strptime(p['date'], format_date_pattern).strftime("%m/%d"), "area": p['area']})

        # ======================
        # POSITIONS
        # ======================
        TITLE_CENTER = (760, 90)
        LEFT_CIRCLE_CENTER = (444, 436)
        RIGHT_CIRCLE_CENTER = (1096, 436)

        LABEL_Y_OFFSET = 140

        BOTTOM_LIST_CONFIG = {
            "start_y": 750,
            "row_spacing": 75,
            "max_per_row": 4,
            "horizontal_padding": 180,
        }

        # ======================
        # LOAD IMAGE & FONTS
        # ======================
        img = Image.open(IMAGE_PATH).convert("RGBA")
        needed_height = 850 + max(0, math.ceil(len(PEOPLE) / 4) - 1) * 75
        if needed_height > img.height:
            expanded = Image.new("RGBA", (img.width, needed_height), img.getpixel((0, img.height - 1)))
            expanded.paste(img, (0, 0))
            img = expanded
        draw = ImageDraw.Draw(img)

        big_font = ImageFont.truetype(BIG_FONT_PATH, BIG_FONT_SIZE)
        small_font = ImageFont.truetype(SMALL_FONT_PATH, SMALL_FONT_SIZE)
        label_font = ImageFont.truetype(SMALL_FONT_PATH, LABEL_FONT_SIZE)

        type2_font = ImageFont.truetype(BIG_FONT_PATH, FONT_TYPE2)
        small_type2_font = ImageFont.truetype(SMALL_FONT_PATH, FONT_SMALL_TYPE2)

        type3_font = ImageFont.truetype(BIG_FONT_PATH, FONT_TYPE3)
        small_type3_font = ImageFont.truetype(SMALL_FONT_PATH, FONT_SMALL_TYPE3)

        bottom_list_font = ImageFont.truetype(SMALL_FONT_PATH, BOTTOM_LIST_FONT_SIZE)

        # ======================
        # ICON HELPERS
        # ======================
        def resize_keep_ratio(img, target_height):
            w, h = img.size
            scale = target_height / h
            return img.resize((int(w * scale), target_height), Image.LANCZOS)

        def place_icon(icon, base_x, base_y, offset_x, offset_y, absolute=None):
            if absolute:
                img.alpha_composite(icon, absolute)
            else:
                img.alpha_composite(icon, (int(base_x + offset_x), int(base_y + offset_y)))

        def dict_to_bar_text(data):
            return "\n".join(f"{k}: {v}" for k, v in data.items())

        lightning_icon = resize_keep_ratio(
            Image.open(LIGHTNING_ICON_PATH).convert("RGBA"),
            LIGHTNING_HEIGHT
        )

        pepper_icon = resize_keep_ratio(
            Image.open(PEPPER_ICON_PATH).convert("RGBA"),
            PEPPER_HEIGHT
        )

        # ======================
        # TEXT HELPERS
        # ======================
        def draw_centered_compound_text(draw, center, parts, fill):
            widths, heights = [], []

            for text, font in parts:
                bbox = draw.textbbox((0, 0), text, font=font)
                widths.append(bbox[2] - bbox[0])
                heights.append(bbox[3] - bbox[1])

            total_width = sum(widths)
            max_height = max(heights)

            x = center[0] - total_width // 2
            y = center[1] - max_height // 2

            for (text, font), w, h in zip(parts, widths, heights):
                draw.text((x, y + (max_height - h) // 2), text, font=font, fill=fill)
                x += w

            return x, y, max_height

        def draw_centered_text(draw, center, text, font, fill):
            bbox = draw.textbbox((0, 0), text, font=font)
            draw.text((center[0] - (bbox[0] + bbox[2]) / 2,
                       center[1] - (bbox[1] + bbox[3]) / 2),
                    text, font=font, fill=fill)

        def draw_gauge(center, value, unit):
            number = "N/A" if value is None else f"{value:g}"
            size = BIG_FONT_SIZE
            font = big_font
            inline = value is not None and unit.strip() == "%"
            unit_box = draw.textbbox((0,0),unit.strip(),font=small_font)
            unit_width = unit_box[2] - unit_box[0]
            allowance = 195 - (unit_width+10 if inline else 0)
            while draw.textbbox((0, 0), number, font=font)[2] > allowance and size > 24:
                size -= 1
                font = ImageFont.truetype(BIG_FONT_PATH, size)
            if inline:
                box = draw.textbbox((0,0),number,font=font)
                width = box[2]-box[0]
                left = center[0]-(width+10+unit_width)/2
                draw_centered_text(draw,(left+width/2,center[1]-8),number,font,TEXT_COLOR)
                draw_centered_text(draw,(left+width+10+unit_width/2,center[1]-8),unit.strip(),small_font,TEXT_COLOR)
            else:
                draw_centered_text(draw, (center[0], center[1] - 8), number, font, TEXT_COLOR)
            if value is not None and not inline:
                draw_centered_text(draw, (center[0], center[1] + 48), unit.strip(), small_font, TEXT_COLOR)

        def original_icon_position(value, unit, center):
            # Preserve the original release's graphic size and lower-dial anchor,
            # independently of the corrected number layout.
            parts=[(f"{value:g}",big_font),(unit,small_font)]
            boxes=[draw.textbbox((0,0),text,font=font) for text,font in parts]
            width=sum(b[2]-b[0] for b in boxes)
            height=max(b[3]-b[1] for b in boxes)
            return (int(center[0]-width//2+width-80),int(center[1]-height//2+90))

        def draw_centered_stacked_text(draw, center, top, bottom, top_font, bottom_font, fill, spacing=20):
            top_bbox = draw.textbbox((0, 0), top, font=top_font)
            top_h = top_bbox[3] - top_bbox[1]

            draw.text((center[0] - top_bbox[2] // 2, center[1] - top_h // 2),
                    top, font=top_font, fill=fill)

            draw.text((center[0] - draw.textbbox((0, 0), bottom, font=bottom_font)[2] // 2,
                    center[1] + top_h // 2 + spacing),
                    bottom, font=bottom_font, fill=fill)
            
        # ======================
        # DRAW TITLE
        # ======================
        title_font = type3_font
        title_size = FONT_TYPE3
        while draw.textbbox((0, 0), TITLE_DATA["top"], font=title_font)[2] > img.width - 120 and title_size > 12:
            title_size -= 1
            title_font = ImageFont.truetype(BIG_FONT_PATH, title_size)
        draw_centered_stacked_text(
            draw,
            TITLE_CENTER,
            TITLE_DATA["top"],
            f'{TITLE_DATA["bottom"]} • {datetime.now().strftime("%m/%d/%y")}',
            title_font,
            small_type3_font,
            TEXT_COLOR,
            spacing=30
        )

        # ======================
        # LEFT GAUGE
        # ======================
        time_data = GAUGE_DATA["time"]

        draw_gauge(LEFT_CIRCLE_CENTER, time_data["value"], time_data["unit"])

        if time_data["value"] is not None and time_data["value"] < self.lightning_time:
            img.alpha_composite(lightning_icon, original_icon_position(time_data["value"],time_data["unit"],(440,410)))

        draw_centered_text(
            draw,
            (LEFT_CIRCLE_CENTER[0], LEFT_CIRCLE_CENTER[1] + LABEL_Y_OFFSET),
            time_data["label"],
            label_font,
            TEXT_COLOR
        )

        # ======================
        # RIGHT GAUGE
        # ======================
        found_data = GAUGE_DATA["found"]

        draw_gauge(RIGHT_CIRCLE_CENTER, found_data["value"], found_data["unit"])

        if found_data["value"] >= self.hot_found:
            img.alpha_composite(pepper_icon, original_icon_position(found_data["value"],found_data["unit"],(1070,410)))

        draw_centered_text(
            draw,
            (RIGHT_CIRCLE_CENTER[0], RIGHT_CIRCLE_CENTER[1] + LABEL_Y_OFFSET),
            found_data["label"],
            label_font,
            TEXT_COLOR
        )

        # ======================
        # MIDDLE STATS
        # ======================
        for stat in MIDDLE_STATS:
            draw_centered_stacked_text(
                draw,
                stat["center"],
                stat["top"],
                stat["bottom"],
                type3_font if stat.get("big") else type2_font,
                small_type3_font if stat.get("big") else small_type2_font,
                TEXT_COLOR,
                spacing=15
            )

        # ======================
        # BOTTOM LIST
        # ======================

        draw_centered_text(draw, (760, BOTTOM_LIST_CONFIG["start_y"] - 75),
                           "Unattempted Referrals", bottom_list_font, TEXT_COLOR)

        rows = math.ceil(len(PEOPLE) / BOTTOM_LIST_CONFIG["max_per_row"])

        for row in range(rows):
            row_people = PEOPLE[row * BOTTOM_LIST_CONFIG["max_per_row"]:(row + 1) * BOTTOM_LIST_CONFIG["max_per_row"]]
            count = len(row_people)

            usable_width = img.width - 2 * BOTTOM_LIST_CONFIG["horizontal_padding"]
            spacing = usable_width // max(count, 1)

            y = BOTTOM_LIST_CONFIG["start_y"] + row * BOTTOM_LIST_CONFIG["row_spacing"]

            for i, person in enumerate(row_people):
                x = BOTTOM_LIST_CONFIG["horizontal_padding"] + spacing // 2 + i * spacing
                name_font = label_font
                area_font = small_type2_font
                for size in range(LABEL_FONT_SIZE, 8, -1):
                    name_font = ImageFont.truetype(SMALL_FONT_PATH, size)
                    if draw.textbbox((0, 0), person["name"], font=name_font)[2] <= spacing - 15:
                        break
                for size in range(FONT_SMALL_TYPE2, 7, -1):
                    area_font = ImageFont.truetype(SMALL_FONT_PATH, size)
                    if draw.textbbox((0, 0), f'{person["date"]} - {person["area"]}', font=area_font)[2] <= spacing - 15:
                        break

                draw_centered_stacked_text(
                    draw,
                    (x, y),
                    person["name"],
                    f'{person["date"]} • {person["area"]}',
                    name_font,
                    area_font,
                    TEXT_COLOR,
                    spacing=10
                )

        # ======================
        # SAVE
        # ======================
        #output_path = output_path.replace("/", "-")
        img.save(output_path)
