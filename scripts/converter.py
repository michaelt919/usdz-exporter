import os
import sys
import struct
from pygltflib import GLTF2, DATA_URI_HEADER
from pxr import Sdf, Usd, UsdShade, UsdGeom

ROOT_FORM = Sdf.Path('/root')
MESH = ROOT_FORM.AppendChild('mesh')
MATERIAL = ROOT_FORM.AppendChild('material')

UV = MATERIAL.AppendChild('st')
SHADER = MATERIAL.AppendChild('pbr')
NORMAL = MATERIAL.AppendChild('normal')
DIFFUSE = MATERIAL.AppendChild('diffuse')
SPECULAR = MATERIAL.AppendChild('specular')
ROUGHNESS = MATERIAL.AppendChild('roughness')

TEXTURE_PATH = 'textures'


def gen_uv_texture(
            stage,
            uv,
            node_path,
            resource_path):
        print(f'Binding {resource_path}...')
        uv_texture = UsdShade.Shader.Define(stage, node_path)
        uv_texture.CreateIdAttr('UsdUVTexture')
        uv_texture.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(resource_path)
        uv_texture.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(), 'result')
        return uv_texture


def gen_normal(
            stage,
            uv,
            texture_format):
        normal = gen_uv_texture(stage, uv, NORMAL, f'{TEXTURE_PATH}/normal.{texture_format}')
        normal.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
        normal.CreateInput('wrapS', Sdf.ValueTypeNames.Token).Set('black')
        normal.CreateInput('wrapT', Sdf.ValueTypeNames.Token).Set('clamp')
        return normal


def gen_diffuse(
            stage,
            uv,
            texture_format):
        diffuse = gen_uv_texture(stage, uv, DIFFUSE, f'{TEXTURE_PATH}/diffuse.{texture_format}')
        diffuse.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
        return diffuse


def gen_specular(
            stage,
            uv,
            texture_format):
        specular = gen_uv_texture(stage, uv, SPECULAR, f'{TEXTURE_PATH}/specular.{texture_format}')
        specular.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
        return specular


def gen_roughness(
            stage,
            uv,
            texture_format):
        roughness = gen_uv_texture(stage, uv, ROUGHNESS, f'{TEXTURE_PATH}/roughness.{texture_format}')
        roughness.CreateOutput('r', Sdf.ValueTypeNames.Float)
        return roughness


def gen_uv(
            stage,
            material):
        print('Unwraping UV...')
        uv = UsdShade.Shader.Define(stage, UV)
        uv.CreateIdAttr('UsdPrimvarReader_float2')
        uv.CreateInput('varname', Sdf.ValueTypeNames.Token).ConnectToSource(material.GetInput('frame:stPrimvarName'))
        uv.CreateOutput('result', Sdf.ValueTypeNames.Float2)
        return uv


def gen_output(
            stage,
            normal,
            diffuse,
            specular,
            roughness):
        print('Generating PBR output shader...')
        output = UsdShade.Shader.Define(stage, SHADER)
        output.CreateIdAttr('UsdPreviewSurface')
        output.CreateInput('useSpecularWorkflow', Sdf.ValueTypeNames.Int).Set(1)
        output.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(0)
        output.CreateInput('normal', Sdf.ValueTypeNames.Normal3f).ConnectToSource(normal.ConnectableAPI(), 'rgb')
        output.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(diffuse.ConnectableAPI(), 'rgb')
        output.CreateInput('specularColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(specular.ConnectableAPI(),
                                                                                        'rgb')
        output.CreateInput('roughness', Sdf.ValueTypeNames.Float).ConnectToSource(roughness.ConnectableAPI(), 'r')
        return output


def gen_material(
            stage,
            texture_format):
        print('Generating material...')
        material = UsdShade.Material.Define(stage, MATERIAL)
        material.CreateInput('frame:stPrimvarName', Sdf.ValueTypeNames.Token).Set('st')
        material.CreateInput('frame:tangentsPrimvarName', Sdf.ValueTypeNames.Token).Set('tangents')
        material.CreateInput('ior', Sdf.ValueTypeNames.Float).Set(5.0)

        # Find and attach our UV map to our shader
        uv = gen_uv(stage, material)

        # Create our texture shaders
        normal = gen_normal(stage, uv, texture_format)
        diffuse = gen_diffuse(stage, uv, texture_format)
        specular = gen_specular(stage, uv, texture_format)
        roughness = gen_roughness(stage, uv, texture_format)

        # Attach our material inputs to a new output shader
        output = gen_output(stage, normal, diffuse, specular, roughness)
        output.CreateInput('ior', Sdf.ValueTypeNames.Float).ConnectToSource(material.GetInput('ior'))

        # Bind our output shader to the surface material
        material.CreateSurfaceOutput().ConnectToSource(output.ConnectableAPI(), 'surface')
        material.CreateDisplacementOutput().ConnectToSource(output.ConnectableAPI(), 'displacement')
        return material


def gen_model(stage, model_path):
        print('Generating model...')
        xform = UsdGeom.Xform.Define(stage, ROOT_FORM)
        mesh = UsdGeom.Mesh.Define(stage, MESH)

        print('Defining model metadata...')
        stage.SetDefaultPrim(xform.GetPrim())
        stage.SetMetadata('metersPerUnit', 1)
        stage.SetMetadata('upAxis', 'Y')

        gltf = GLTF2.load_binary(model_path)

        # Because for *SOME* reason, pygltflib hardcodes the header, when this split would be simpler
        # Maybe I'll make a PR on their repo later
        for buffer in gltf.buffers:
                buffer.uri = f'{DATA_URI_HEADER}{buffer.uri.split(';base64,')[1]}'

        # There should only ever be one mesh and one primitive
        primitive = gltf.meshes[0].primitives[0]

        # Get point and face data
        print('Translating position and face data...')
        accessor = gltf.accessors[primitive.attributes.POSITION]
        bufferView = gltf.bufferViews[accessor.bufferView]
        buffer = gltf.buffers[bufferView.buffer]
        data = gltf.get_data_from_buffer_uri(buffer.uri)

        # Pull each tuple (in 3s) from the data
        vertices = []
        for i in range(accessor.count):
                index = bufferView.byteOffset + accessor.byteOffset + i * 12
                d = data[index:index + 12]
                v = struct.unpack("<fff", d)
                vertices.append(v)

        # Bind points to USD mesh
        mesh.GetPointsAttr().Set(vertices)
        mesh.GetExtentAttr().Set((accessor.min, accessor.max))
        mesh.GetFaceVertexIndicesAttr().Set(range(accessor.count))
        mesh.GetFaceVertexCountsAttr().Set([3] * int(accessor.count / 3))

        # Get normal data
        print('Translating normal data...')
        accessor = gltf.accessors[primitive.attributes.NORMAL]
        bufferView = gltf.bufferViews[accessor.bufferView]
        buffer = gltf.buffers[bufferView.buffer]
        data = gltf.get_data_from_buffer_uri(buffer.uri)

        # Pull each tuple (in 3s) from the data
        normals = []
        for i in range(accessor.count):
                index = bufferView.byteOffset + accessor.byteOffset + i * 12
                d = data[index:index + 12]
                v = struct.unpack("<fff", d)
                normals.append(v)

        # Bind normals to USD mesh
        mesh.GetNormalsAttr().Set(normals)
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)

        # Get UV data
        print('Translating UV data...')
        accessor = gltf.accessors[primitive.attributes.TEXCOORD_0]
        bufferView = gltf.bufferViews[accessor.bufferView]
        buffer = gltf.buffers[bufferView.buffer]
        data = gltf.get_data_from_buffer_uri(buffer.uri)

        # Pull each tuple (in 2s) from the data
        uv = []
        for i in range(accessor.count):
                index = bufferView.byteOffset + accessor.byteOffset + i * 8
                d = data[index:index + 8]
                x, y = struct.unpack("<ff", d)
                # Invert y-axis
                uv.append((x, 1 - y))

        # Bind UV to ST in USD mesh
        st = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'st',
                Sdf.ValueTypeNames.TexCoord2fArray,
                UsdGeom.Tokens.faceVarying
        )
        st.Set(uv)
        st.SetIndices(range(accessor.count))

        # Get tangent data
        print('Translating tangent data...')
        accessor = gltf.accessors[primitive.attributes.TANGENT]
        bufferView = gltf.bufferViews[accessor.bufferView]
        buffer = gltf.buffers[bufferView.buffer]
        data = gltf.get_data_from_buffer_uri(buffer.uri)

        # Pull each tuple (in 4s) from the data
        tangent = []
        for i in range(accessor.count):
                index = bufferView.byteOffset + accessor.byteOffset + i * 16
                d = data[index:index + 16]
                v = struct.unpack("<ffff", d)
                tangent.append(v)

        # Bind UV to ST in USD mesh
        tangents = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'tangents',
                Sdf.ValueTypeNames.Float4Array,
                UsdGeom.Tokens.vertex
        )
        tangents.Set(tangent)
        tangents.SetIndices(range(accessor.count))

        print('Applying miscellaneous attributes...')
        mesh.GetSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        return mesh


def export_usdz(
            base_name,
            texture_format,
            normal_path,
            diffuse_path,
            specular_path,
            roughness_path):
        print('Compressing usdz archive...')
        # Create a writer for the target usdz file
        writer = Sdf.ZipFileWriter.CreateNew(f'{base_name}.usdz')

        # Add files to the archive
        writer.AddFile(f'{base_name}.usda', 'model.usda')
        writer.AddFile(normal_path, f'textures/normal.{texture_format}')
        writer.AddFile(diffuse_path, f'textures/diffuse.{texture_format}')
        writer.AddFile(specular_path, f'textures/specular.{texture_format}')
        writer.AddFile(roughness_path, f'textures/roughness.{texture_format}')

        # Finalize the file
        writer.Save()
        pass


def cleanup(base_name):
        print('Removing temporary files...')
        os.remove(f'{base_name}.usda')
        pass


def main():
        if len(sys.argv) < 7:
                print('Invalid number of arguments.')
                sys.exit(1)
        glb_file = sys.argv[1]
        texture_format = sys.argv[2]
        normal_texture = sys.argv[3]
        diffuse_texture = sys.argv[4]
        specular_texture = sys.argv[5]
        roughness_texture = sys.argv[6]

        # Get the base name of the glb file
        base_name = glb_file.rsplit('.', 1)[0]

        # Open the new usd on the stage
        stage = Usd.Stage.CreateNew(f'{base_name}.usda')

        # Generate mesh from glb
        mesh = gen_model(stage, glb_file)

        # Generate our new material
        material = gen_material(stage, texture_format)

        # Bind the material to the mesh
        UsdShade.MaterialBindingAPI(mesh).Bind(material)

        # Write our material graph to the usda file
        stage.Export(f'{base_name}.usda')

        # Pack and export our model
        export_usdz(base_name, texture_format, normal_texture, diffuse_texture, specular_texture, roughness_texture)

        # Clean up no longer needed files
        cleanup(base_name)
        print('Finished!')
        sys.exit(0)


if __name__ == '__main__':
        main()
