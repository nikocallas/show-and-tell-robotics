def run(scene):
    for obj in scene.objects(fragile=False):
        pick(obj)
        place("middle_bin")
