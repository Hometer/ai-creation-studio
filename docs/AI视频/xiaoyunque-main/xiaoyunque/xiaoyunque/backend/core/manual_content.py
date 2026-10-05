"""Validate the existing manual JSON editors without normalizing away metadata."""
import math
import json
from core.asset_library import AssetLibraryError

SCRIPT_TEXT = {'title','logline','overall_style','mood','beat_sheet'}
SCRIPT_LISTS = {'characters','settings','scenes','episodes','genre'}

def invalid():
    raise AssetLibraryError(422, 'INVALID_MANUAL_CONTENT', '编辑内容结构不支持，请保留草稿并核对字段类型')

def validate_replacement(stage, before, after):
    count = 0
    def json_value(value, depth=0):
        nonlocal count
        count += 1
        if depth > 20 or count > 20000: invalid()
        if value is None or isinstance(value, (str, bool, int)): return
        if isinstance(value, float):
            if not math.isfinite(value): invalid()
        elif isinstance(value, list):
            for item in value: json_value(item, depth+1)
        elif isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key,str) or key in {'__proto__','prototype','constructor'}: invalid()
                json_value(item,depth+1)
        else: invalid()
    json_value(after)
    def strings(value):
        if not isinstance(value,list) or any(not isinstance(i,str) for i in value): invalid()
    def objects(obj, name):
        value=obj.get(name, [])
        if not isinstance(value,list) or any(not isinstance(i,dict) for i in value): invalid()
        return value
    def text(obj, names):
        for name in names:
            if name in obj and not isinstance(obj[name],str): invalid()
    def number(obj,name,integer=False):
        if name in obj and (isinstance(obj[name],bool) or not isinstance(obj[name],(int,float)) or not math.isfinite(obj[name]) or obj[name] < 0 or (integer and (not isinstance(obj[name],int) or obj[name] < 1))): invalid()
    def protected(old,new,allowed):
        for key in old.keys() | new.keys():
            if key not in allowed and (key not in old or key not in new or json.dumps(old[key],sort_keys=True,allow_nan=False) != json.dumps(new[key],sort_keys=True,allow_nan=False)): invalid()
    if not isinstance(after,dict): invalid()
    if stage == 'script_generation':
        # Old script page edits the root payload, retaining all control fields.
        protected(before,after,SCRIPT_TEXT | SCRIPT_LISTS)
        text(after,SCRIPT_TEXT)
        if 'genre' in after: strings(after['genre'])
        for character in objects(after,'characters'):
            text(character,{'name','character_id','description','motivation','role','age','species','occupation','arc_description'})
            if 'personality' in character: strings(character['personality'])
        for setting in objects(after,'settings'): text(setting,{'name','description'})
        for scene in objects(after,'scenes'):
            text(scene,{'location','plot'});number(scene,'scene_number',True)
            if 'characters' in scene: strings(scene['characters'])
        for episode in objects(after,'episodes'):
            text(episode,{'act_title','episode_title','content'});number(episode,'episode_number',True)
    else:
        wrapped = 'episodes' not in before and isinstance(before.get('payload'),dict) and 'episodes' in before['payload']
        protected(before,after,{'payload'} if wrapped else {'episodes'})
        old = before['payload'] if wrapped else before
        body = after.get('payload') if wrapped else after
        if not isinstance(body,dict): invalid()
        if wrapped: protected(old,body,{'episodes'})
        segment_ids=set()
        for episode in objects(body,'episodes'):
            text(episode,{'episode_title','act_title'});number(episode,'episode_number',True)
            for segment in objects(episode,'segments'):
                text(segment,{'segment_id','location','visual_prompt'})
                sid=segment.get('segment_id')
                if not isinstance(sid,str) or not sid or sid in segment_ids: invalid()
                segment_ids.add(sid)
                if 'characters' in segment: strings(segment['characters'])
                for name in ['segment_number','episode_number']: number(segment,name,True)
                number(segment,'total_duration')
                numbers=set()
                for shot in objects(segment,'shots'):
                    text(shot,{'shot_type','content','plot','dialogue'})
                    number(shot,'duration');number(shot,'shot_number',True)
                    n=shot.get('shot_number')
                    if not isinstance(n,int) or isinstance(n,bool) or n < 1 or n in numbers: invalid()
                    numbers.add(n)
